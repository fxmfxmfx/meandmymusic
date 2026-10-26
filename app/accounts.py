import asyncio
import time

import yaml
from fastapi import HTTPException

from app.vault import CredentialVault

RUNTIME_CONFIG = "/run/slskd/slskd.yml"


class SoulseekAccount:
    def __init__(self, settings, client):
        self.settings, self.client = settings, client
        self.vault = CredentialVault(settings)
        self.lock = asyncio.Lock()
        self.error = None
        self.expected_username = None
        self.last_attempt = 0
        self.restore_enabled = self.vault.saved
        self.poll_seconds = 12

    async def status(self):
        result = {"managed": self.settings.slskd_managed_auth, "api_reachable": False, "connected": False,
                  "logged_in": False, "transitioning": False, "username": None, "saved": self.vault.saved,
                  "busy": self.lock.locked(), "error": self.error}
        if not self.settings.slskd_key:
            return result
        try:
            raw = await self.client.request("GET", "/server", timeout=3)
            result.update(api_reachable=True, connected=raw.get("isConnected") is True,
                          logged_in=raw.get("isLoggedIn") is True, transitioning=raw.get("isTransitioning") is True,
                          username=raw.get("username") if raw.get("isLoggedIn") is True else None)
        except HTTPException as error:
            result["error"] = error.detail
        return result

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
            if (options.get("soulseek", {}).get("username") or None) == (username or None):
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
            try:
                await self.configure(username, password)
                await self.client.request("PUT", "/server")
                deadline = time.monotonic() + self.poll_seconds
                while time.monotonic() < deadline:
                    raw = await self.client.request("GET", "/server", timeout=3)
                    if raw.get("isLoggedIn") is True and raw.get("username") == username:
                        if remember:
                            self.vault.save(username, password)
                        else:
                            self.vault.forget()
                        self.expected_username = username
                        self.restore_enabled = remember
                        return {"logged_in": True, "username": username, "saved": remember, "message": "Вход в сеть Soulseek подтверждён сервером."}
                    await asyncio.sleep(.5)
                raise HTTPException(504, "Soulseek не подтвердил вход. Проверьте логин/пароль, доступ к сети и отсутствие другой активной сессии. Для нового аккаунта попробуйте свободное имя.")
            except (HTTPException, OSError, ValueError) as error:
                self.error = error.detail if isinstance(error, HTTPException) else "Не удалось сохранить защищённую учётку Soulseek"
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
