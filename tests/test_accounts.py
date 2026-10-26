import asyncio
import json
import os
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from app.accounts import RUNTIME_CONFIG, SoulseekAccount
from app.vault import CredentialVault


class FakeSlskd:
    """Pinned API contract, including async YAML reload and true network login flags."""
    def __init__(self):
        self.config = {"soulseek": {"username": None, "password": None}, "shares": {"directories": ["/music"]}}
        self.location = RUNTIME_CONFIG
        self.logged_in = False
        self.reject = False
        self.calls = []
        self.logs = []
        self.server_username = None

    async def admin_token(self):
        return "test-admin-jwt"

    async def request(self, method, path, data=None, **kwargs):
        self.calls.append((method, path, data))
        if path == "/logs/live":
            return self.logs
        if path == "/options/yaml/location":
            return self.location
        if path == "/options/yaml":
            if method == "GET":
                return yaml.safe_dump(self.config)
            self.config = yaml.safe_load(data)
            return {}
        if path == "/options":
            return {"soulseek": {"username": self.config["soulseek"]["username"], "password": "*****", "connection": self.config["soulseek"].get("connection", {})}}
        if path == "/server":
            if method == "DELETE":
                self.logged_in = False
                return {}
            if method == "PUT":
                self.logged_in = not self.reject
                return {}
            return {"isConnected": True, "isLoggedIn": self.logged_in, "isTransitioning": False,
                    "username": self.server_username, "password": "must-not-be-forwarded"}
        raise AssertionError(f"Unexpected upstream route: {method} {path}")


@pytest.fixture
def account(settings, monkeypatch):
    settings.slskd_managed_auth = True
    settings.slskd_key = "a" * 32
    value = SoulseekAccount(settings, FakeSlskd())
    value.poll_seconds = .01
    async def no_sleep(_):
        pass
    monkeypatch.setattr("app.accounts.asyncio.sleep", no_sleep)
    return value


@pytest.mark.asyncio
async def test_confirmed_login_encrypts_both_credentials_and_preserves_config(account, settings):
    result = await account.login("private-username", "private-password")
    assert result["logged_in"] is True
    assert account.vault.load() == {"username": "private-username", "password": "private-password"}
    assert account.client.config["shares"]["directories"] == ["/music"]
    assert "password" not in result
    status = await account.status()
    assert status["logged_in"] is True and status["username"] == "private-username"
    assert "must-not-be-forwarded" not in json.dumps(status)
    for file in settings.state.iterdir():
        assert b"private-password" not in file.read_bytes()
        assert b"private-username" not in file.read_bytes()
    assert os.stat(account.vault.path).st_mode & 0o777 == 0o600
    assert os.stat(settings.state / "vault.salt").st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_explicit_different_logged_in_username_is_not_accepted(account):
    account.client.server_username = "another-user"
    with pytest.raises(HTTPException) as error:
        await account.login("requested-user", "requested-password")
    assert error.value.status_code == 504
    assert not account.vault.saved


@pytest.mark.asyncio
async def test_missing_server_username_uses_options_only_after_confirmed_login(account):
    account.client.config["soulseek"]["username"] = "configured-user"
    raw = await account.server_status()
    assert raw["isLoggedIn"] is False
    assert not any(path == "/options" for _, path, _ in account.client.calls)
    account.client.logged_in = True
    raw = await account.server_status()
    assert raw["username"] == "configured-user"


@pytest.mark.asyncio
async def test_transport_connected_is_not_success_and_failed_credentials_are_cleared(account):
    account.client.reject = True
    with pytest.raises(HTTPException) as error:
        await account.login("rejected-user", "wrong-password")
    assert error.value.status_code == 504
    assert not account.vault.saved
    assert account.client.config["soulseek"]["username"] is None
    assert account.client.config["soulseek"]["password"] is None
    assert account.client.config["soulseek"]["connection"]["proxy"]["password"] is None
    assert not account.restore_enabled
    assert (await account.status())["logged_in"] is False


@pytest.mark.asyncio
async def test_no_remember_and_logout_clear_encrypted_and_ram_credentials(account):
    await account.login("test-user", "test-password", remember=False)
    assert not account.vault.saved
    result = await account.logout()
    assert result == {"logged_in": False, "saved": False}
    assert account.client.config["soulseek"]["password"] is None
    writes = [data for method, path, data in account.client.calls if method == "PUT" and path == "/options/yaml"]
    assert yaml.safe_load(writes[-1])["soulseek"]["password"] is None
    assert yaml.safe_load(writes[-2])["soulseek"]["password"] is None


@pytest.mark.asyncio
async def test_refuses_plaintext_config_on_persistent_disk(account):
    account.client.location = "/app/slskd.yml"
    with pytest.raises(HTTPException) as error:
        await account.login("test-user", "test-password")
    assert error.value.status_code == 409
    assert not any(method == "PUT" and path == "/options/yaml" for method, path, _ in account.client.calls)


def test_vault_rejects_tamper_and_changed_app_password(settings):
    vault = CredentialVault(settings)
    vault.save("username", "password")
    settings.password = "different-archive-password"
    with pytest.raises(ValueError, match="расшифровать"):
        CredentialVault(settings).load()
    vault.path.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="расшифровать"):
        vault.load()


@pytest.mark.asyncio
async def test_saved_account_can_reconnect_after_restart(account):
    account.vault.save("saved-user", "saved-password")
    result = await account.reconnect()
    assert result["logged_in"] is True and result["saved"] is True


def test_account_routes_require_manager_auth_and_do_not_echo_bad_payloads(client, auth):
    client.post("/api/logout")
    assert client.get("/api/soulseek/auth/status").status_code == 401
    client.post("/api/login", json={"password": "test-archive-password"})
    response = client.post("/api/soulseek/auth/login", json={"username": "secret-username", "password": "secret-password" * 100})
    assert response.status_code == 422
    assert "secret-password" not in response.text
    assert "secret-username" not in response.text
    response = client.post("/api/soulseek/auth/register", json={"username": "user", "password": "private", "password_confirm": "other"})
    assert response.status_code == 422 and "не совпадают" in response.json()["detail"]
    assert client.post("/api/soulseek/auth/login", json={"username": "u", "password": "p"}).status_code == 409


def test_verified_registration_uses_same_server_confirmation(auth, monkeypatch):
    calls = []
    async def login(username, password, remember):
        calls.append((username, password, remember))
        return {"logged_in": True, "username": username, "saved": remember}
    monkeypatch.setattr(auth.app.state.account, "login", login)
    response = auth.post("/api/soulseek/auth/register", json={"username": "new-user", "password": "private", "password_confirm": "private", "remember": False})
    assert response.status_code == 200 and response.json()["logged_in"] is True
    assert calls == [("new-user", "private", False)]


def test_upgrade_removes_legacy_secrets_without_backups(tmp_path):
    from scripts.upgrade import upgrade
    path = tmp_path / ".env"
    path.write_text("APP_PASSWORD=keep-this-password\nSLSKD_API_KEY=keep-this-key\nSOULSEEK_USERNAME='private-name'\nSOULSEEK_PASSWORD='private-pass'\nAPP_BIND=127.0.0.1\n")
    assert upgrade(path, lan=True) == 2
    text = path.read_text()
    assert "private-name" not in text and "private-pass" not in text
    assert "APP_PASSWORD=keep-this-password" in text and "SLSKD_API_KEY=keep-this-key" in text
    assert "APP_BIND=0.0.0.0" in text
    assert list(tmp_path.iterdir()) == [path]
    assert path.stat().st_mode & 0o777 == 0o600


def test_compose_uses_valid_key_syntax_tmpfs_and_no_soulseek_secrets():
    text = Path("compose.yaml").read_text()
    assert "role=Administrator;${SLSKD_API_KEY" in text
    assert "SOULSEEK_USERNAME" not in text and "SOULSEEK_PASSWORD" not in text
    assert "SLSKD_SLSK_USERNAME" not in text and "SLSKD_SLSK_PASSWORD" not in text
    assert "SLSKD_CONFIG: /run/slskd/slskd.yml" in text
    assert "/run/slskd:rw,noexec,nosuid,nodev" in text


def test_startup_socket_denial_explains_host_policy(monkeypatch, capsys):
    from app import run
    def denied():
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr(run.socket, "socketpair", denied)
    assert run.main() == 1
    message = capsys.readouterr().err
    assert "socketpair" in message and "AppArmor" in message and "before opening music folders" in message


@pytest.mark.asyncio
async def test_login_reads_only_fresh_failure_logs_and_hides_raw_secrets(account):
    account.client.logs = [{"timestamp": "old", "context": "slskd.Application", "message": "INVALIDPASS stale-secret"}]
    account.client.reject = True
    original = account.client.request
    async def request(method, path, data=None, **kwargs):
        result = await original(method, path, data, **kwargs)
        if method == "PUT" and path == "/server":
            account.client.logs.append({"timestamp": "new", "context": "slskd.Application", "message": "Login failed: INVALIDPASS current-secret"})
        return result
    account.client.request = request
    with pytest.raises(HTTPException) as error:
        await account.login("rejected", "password")
    assert error.value.info["code"] == "INVALID_PASSWORD"
    status = await account.status()
    assert status["failure"]["code"] == "INVALID_PASSWORD"
    assert "current-secret" not in json.dumps(status) and "stale-secret" not in json.dumps(status)
    assert account.client.config["soulseek"]["password"] is None


@pytest.mark.asyncio
async def test_stale_invalid_password_log_is_not_the_current_login_error(account):
    account.client.logs = [{"timestamp": "old", "context": "slskd.Application", "message": "INVALIDPASS"}]
    account.client.reject = True
    with pytest.raises(HTTPException) as error:
        await account.login("user", "password")
    assert error.value.info["code"] == "LOGIN_TIMEOUT"
