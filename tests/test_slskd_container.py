"""Opt-in test against the pinned real slskd container. Never logs in to Soulseek."""
import json
import os
from pathlib import Path
import subprocess

import httpx
import pytest

from app.accounts import SoulseekAccount
from app.config import Settings
from app.integrations import Soulseek

pytestmark = pytest.mark.skipif(os.getenv("MMM_TEST_SLSKD") != "1", reason="Requires docker compose running with a generated .env")


@pytest.mark.asyncio
async def test_pinned_slskd_key_admin_login_config_reload_and_tmpfs(tmp_path):
    # setup.py generates unquoted random values for these two local CI secrets.
    values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines() if line and not line.startswith("#") and "=" in line)
    settings = Settings(library=tmp_path / "music", inbox=tmp_path / "inbox", state=tmp_path / "state",
                        password="integration-vault-password", slskd_key=values["SLSKD_API_KEY"],
                        slskd_url="http://127.0.0.1:5030", slskd_managed_auth=True,
                        slskd_admin_password=values["APP_PASSWORD"])
    settings.prepare()
    settings.outbound_proxy = "socks5://proxy-user:temporary-proxy-password@proxy.invalid:1080"
    client = Soulseek(settings)
    account = SoulseekAccount(settings, client)
    assert isinstance(await client.request("GET", "/application"), dict)
    container = subprocess.check_output(["docker", "compose", "ps", "-q", "slskd"], text=True).strip()
    inspected = json.loads(subprocess.check_output(["docker", "inspect", container], text=True))[0]
    # Compose's short tmpfs syntax is represented under HostConfig.Tmpfs,
    # not consistently under Mounts. Verify both configuration and the live mount.
    assert "/run/slskd" in inspected["HostConfig"].get("Tmpfs", {})
    mounts = subprocess.check_output(["docker", "exec", container, "cat", "/proc/mounts"], text=True)
    assert any(parts[1:3] == ["/run/slskd", "tmpfs"] for parts in (line.split() for line in mounts.splitlines()))
    username, password = "mmm-api-contract-test", "temporary-contract-password"
    try:
        # configure() stops the connection watchdog. No PUT /server is issued:
        # the test credentials are never used on the public Soulseek network.
        await account.configure(username, password)
        token = await client.admin_token()
        options = await client.request("GET", "/options", bearer=token)
        assert options["soulseek"]["username"] == username
        assert options["soulseek"]["password"] != password
        proxy = options["soulseek"]["connection"]["proxy"]
        assert proxy["enabled"] is True and proxy["address"] == "proxy.invalid" and proxy["port"] == 1080
        assert proxy["password"] != "temporary-proxy-password"
        assert isinstance(await client.request("GET", "/logs/live"), list)
        assert (await client.request("GET", "/server"))["isLoggedIn"] is False
        for file in Path(values.get("SLSKD_STATE_PATH", "data/slskd")).rglob("*"):
            if file.is_file():
                assert password.encode() not in file.read_bytes(), f"Plaintext password found in persistent file {file.name}"
                assert b"temporary-proxy-password" not in file.read_bytes()
    finally:
        await account.configure()
    async with httpx.AsyncClient(trust_env=False) as web:
        response = await web.get("http://127.0.0.1:8080/api/health")
    assert response.status_code == 200
