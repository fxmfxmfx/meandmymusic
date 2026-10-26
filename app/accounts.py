import asyncio
import time
import hashlib
import json

import yaml
from fastapi import HTTPException

from app.vault import CredentialVault
from app.errors import ConnectionFailure, classify_connection_log
from app.proxy import parse_proxy, proxy_status, probe_proxy

RUNTIME_CONFIG = "/run/slskd/slskd.yml"


class SoulseekAccount:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.vault = CredentialVault(settings)
        self.lock = asyncio.Lock()
        self.error = None
        self.failure = None
        self.phase = "idle"
        self.server_host, self.server_port = "vps.slsknet.org", 2271
        self.expected_username = None
        self.last_attempt = 0
        self.restore_enabled = self.vault.saved
        self.poll_seconds = settings.soulseek_login_timeout

    async def server_status(self):
        raw = await self.client.request("GET", "/server", timeout=3)
        if raw.get("isLoggedIn") is True and not raw.get("username"):
            # slskd 0.26.2 declares ServerState.Username but never populates it:
            # Application.Client_StateChanged writes the name to State.User.
            # The REST endpoint exposes only State.Server. After a confirmed
            # network login, use the current configured account (configure()
            # disconnects the previous session before changing credentials).
            options = await self.client.request("GET", "/options", timeout=3)
            raw = {**raw, "username": options.get("soulseek", {}).get("username")}
        return raw

    async def status(self):
        result = {"managed": self.settings.slskd_managed_auth, "api_reachable": False, "connected": False,
                  "logged_in": False, "transitioning": False, "username": None, "saved": self.vault.saved,
                  "busy": self.lock.locked(), "error": self.error, "failure": self.failure,
                  "stage": self.phase, "proxy": proxy_status(self.settings.outbound_proxy)}
        if not self.settings.slskd_key:
            return result
        try:
            raw = await self.server_status()
            result.update(api_reachable=True, connected=raw.get("isConnected") is True,
                          logged_in=raw.get("isLoggedIn") is True, transitioning=raw.get("isTransitioning") is True,
                          username=raw.get("username") if raw.get("isLoggedIn") is True else None)
            if result["logged_in"]:
                result["error"], result["failure"] = None, None
        except HTTPException as error:
            result["error"] = error.detail
            if isinstance(error, ConnectionFailure):
                result["failure"] = error.info
        return result

    async def connection_logs(self):
        try:
            records = await self.client.request("GET", "/logs/live", timeout=3)
            return [r for r in records if isinstance(r, dict)] if isinstance(records, list) else []
        except HTTPException:
            return []

    @staticmethod
    def record_key(record):
        return hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()

    async def new_failure(self, seen):
        records = await self.connection_logs()
        for record in reversed(records):
            key = self.record_key(record)
            if key in seen:
                continue
            seen.add(key)
            context = str(record.get("context", record.get("Context", ""))).lower()
            if context and not any(value in context for value in ("application", "connectionwatchdog", "soulseek")):
                continue
            failure = classify_connection_log(str(record.get("message", record.get("Message", ""))))
            if failure:
                return failure
        return None

    def require_managed(self):
        if not self.settings.slskd_managed_auth:
            raise HTTPException(409, "Вход из менеджера выключен. Для штатного Compose выполните обновление конфигурации; внешний slskd требует отдельной настройки временного конфига в tmpfs.")

    async def configure(self, username=None, password=None):
        """Only write credentials to the pinned slskd tmpfs configuration, never /app."""
        token = await self.client.admin_token()
        location = await self.client.request("GET", "/options/yaml/location", bearer=token)
        if location != RUNTIME_CONFIG:
            raise HTTPException(409, "slskd должен использовать /run/slskd/slskd.yml на tmpfs. Запись учётки в постоянный конфиг запрещена.")
        await self.client.request("DELETE", "/server", "Account settings changed")
        text = await self.client.request("GET", "/options/yaml", bearer=token)
        try:
            config = yaml.safe_load(text) or {}
            if not isinstance(config, dict):
                raise ValueError
            config["soulseek"] = {**(config.get("soulseek") or {}), "username": username, "password": password}
            proxy = parse_proxy(self.settings.outbound_proxy) if username is not None else None
            connection = config["soulseek"].setdefault("connection", {}) or {}
            config["soulseek"]["connection"] = connection
            connection["proxy"] = proxy.slskd_options() if proxy else {"enabled": False, "username": None, "password": None}
            payload = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
        except (ValueError, TypeError, yaml.YAMLError):
            raise HTTPException(502, "slskd вернул некорректную конфигурацию") from None
        await self.client.request("PUT", "/options/yaml", payload, bearer=token)
        if username is None:
            # slskd keeps the previous YAML in .bak. Clear that RAM-only backup too.
            await self.client.request("PUT", "/options/yaml", payload, bearer=token)
        # slskd reloads YAML asynchronously through its file watcher.
        for _ in range(20):
            await asyncio.sleep(.25)
            options = await self.client.request("GET", "/options", bearer=token)
            active_proxy = options.get("soulseek", {}).get("connection", {}).get("proxy", {})
            proxy_ready = (active_proxy.get("enabled") is True and active_proxy.get("address") == proxy.host and active_proxy.get("port") == proxy.port) if proxy else not active_proxy.get("enabled")
            if (options.get("soulseek", {}).get("username") or None) == (username or None) and proxy_ready:
                soulseek = options.get("soulseek", {})
                self.server_host, self.server_port = soulseek.get("address") or "vps.slsknet.org", soulseek.get("port") or 2271
                # Same-username password edits also need time for the file watcher.
                await asyncio.sleep(.75)
                return
        raise HTTPException(504, "slskd не применил настройки. Проверьте, что наблюдение за конфигом включено.")

    async def login(self, username, password, remember=True, *, restoring=False):
        self.require_managed()
        if self.lock.locked():
            raise HTTPException(409, "Уже выполняется подключение. Дождитесь результата.")
        if not restoring and time.monotonic() - self.last_attempt < 3:
            raise HTTPException(429, "Подождите несколько секунд перед новой попыткой.")
        async with self.lock:
            self.last_attempt = time.monotonic()
            self.restore_enabled = False
            self.error = None
            self.failure = None
            try:
                self.phase = "configuration"
                await self.configure(username, password)
                proxy = parse_proxy(self.settings.outbound_proxy)
                if proxy:
                    self.phase = "proxy"
                    await probe_proxy(proxy, self.server_host, self.server_port)
                seen = {self.record_key(record) for record in await self.connection_logs()}
                self.phase = "login"
                await self.client.request("PUT", "/server")
                deadline = time.monotonic() + self.poll_seconds
                while time.monotonic() < deadline:
                    raw = await self.server_status()
                    if raw.get("isLoggedIn") is True and raw.get("username") == username:
                        if remember:
                            self.vault.save(username, password)
                        else:
                            self.vault.forget()
                        self.expected_username = username
                        self.restore_enabled = remember
                        self.phase = "connected"
                        return {"logged_in": True, "username": username, "saved": remember, "message": "Вход в сеть Soulseek подтверждён сервером."}
                    failure = await self.new_failure(seen)
                    if failure:
                        raise failure
                    await asyncio.sleep(.5)
                raise ConnectionFailure("LOGIN_TIMEOUT", "Soulseek не подтвердил вход за отведённое время.", stage="login", hint="Сервер не сообщил распознаваемую причину отказа. Проверь сеть, учётку и последние логи slskd; блокировка по региону пока не подтверждена.", status=504)
            except (HTTPException, OSError, ValueError) as error:
                self.error = error.detail if isinstance(error, HTTPException) else "Не удалось сохранить защищённую учётку Soulseek"
                self.failure = error.info if isinstance(error, ConnectionFailure) else {"code": "ACCOUNT_ERROR", "stage": self.phase, "message": self.error, "hint": "Проверь конфигурацию и логи slskd."}
                self.phase = "failed"
                # Stop retrying rejected credentials and remove them from RAM YAML.
                try:
                    await self.configure()
                except Exception:
                    try:
                        await self.client.request("DELETE", "/server", "Login was not confirmed")
                    except Exception:
                        pass
                if isinstance(error, HTTPException):
                    raise
                raise HTTPException(500, self.error) from None

    async def logout(self):
        self.require_managed()
        if self.lock.locked():
            raise HTTPException(409, "Дождитесь завершения подключения")
        async with self.lock:
            self.restore_enabled = False
            self.expected_username = None
            self.vault.forget()
            self.error = None
            self.failure = None
            self.phase = "idle"
            await self.configure()
        return {"logged_in": False, "saved": False}

    async def reconnect(self):
        self.require_managed()
        try:
            value = self.vault.load()
        except ValueError as error:
            self.restore_enabled = False
            raise HTTPException(409, str(error)) from None
        if not value:
            raise HTTPException(409, "Сохранённой учётки нет. Введите логин и пароль.")
        return await self.login(value["username"], value["password"], restoring=True)

    async def maintain(self):
        """Restore after a slskd restart, without repeatedly kicking other clients."""
        while True:
            try:
                if self.settings.slskd_managed_auth and self.restore_enabled and not self.lock.locked():
                    current = await self.status()
                    if current["api_reachable"] and not current["logged_in"] and not current["transitioning"]:
                        token = await self.client.admin_token()
                        options = await self.client.request("GET", "/options", bearer=token)
                        # Empty RAM config identifies a restart. A disconnected account
                        # with nonempty config is left alone (e.g. logged in elsewhere).
                        if not options.get("soulseek", {}).get("username"):
                            await self.reconnect()
            except (HTTPException, ValueError) as error:
                self.error = error.detail if isinstance(error, HTTPException) else str(error)
                # Service unavailable can be retried; rejected credentials cannot.
                if isinstance(error, ValueError):
                    self.restore_enabled = False
            await asyncio.sleep(15)
