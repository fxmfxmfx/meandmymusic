import asyncio
import io
import socket
from urllib.request import Request

import httpx
import pytest

from app.errors import ConnectionFailure, classify_connection_log
from app.proxy import beets_proxy, parse_proxy, probe_proxy, proxy_status


def test_proxy_parser_and_status_hide_credentials(settings):
    value = "socks5://private-user:p%40ss%3Aword@host.docker.internal:1080"
    proxy = parse_proxy(value)
    assert proxy.password == "p@ss:word"
    assert proxy.host == "host.docker.internal"
    assert "private-user" not in str(proxy.status())
    assert "p@ss" not in repr(proxy)
    settings.outbound_proxy = value
    assert value not in repr(settings)
    assert proxy_status("") == {"enabled": False}


@pytest.mark.parametrize("value", ["http://user:private@proxy:8080", "socks5://host", "socks5://host:99999", "socks5://u@host:1080", "socks5://host:1080/path"])
def test_invalid_proxy_is_explained_without_echoing_input(value):
    with pytest.raises(ConnectionFailure) as error:
        parse_proxy(value)
    assert error.value.info["code"] == "PROXY_CONFIG_INVALID"
    assert value not in str(error.value)


@pytest.mark.parametrize("message,code", [
    ("Login failed. Reported failure reason: INVALIDPASS secret-password", "INVALID_PASSWORD"),
    ("INVALIDUSERNAME", "INVALID_USERNAME"),
    ("LoggedInElsewhere", "LOGGED_IN_ELSEWHERE"),
    ("proxy authentication failed: secret-password", "PROXY_AUTH_FAILED"),
    ("No such host is known", "DNS_FAILED"),
    ("Connection refused", "CONNECTION_REFUSED"),
    ("Network is unreachable", "NETWORK_UNREACHABLE"),
    ("Connection timed out", "CONNECTION_TIMEOUT"),
])
def test_log_classification_never_returns_raw_messages(message, code):
    error = classify_connection_log(message)
    assert error.info["code"] == code
    assert "secret-password" not in str(error.info)


@pytest.mark.asyncio
async def test_socks_probe_remote_dns_and_authentication():
    captured = {}
    async def handle(reader, writer):
        try:
            captured["greeting"] = await reader.readexactly(4)
            writer.write(b"\x05\x02");await writer.drain()
            version, length = await reader.readexactly(2)
            captured["user"] = await reader.readexactly(length)
            length = (await reader.readexactly(1))[0]
            captured["pass"] = await reader.readexactly(length)
            writer.write(b"\x01\x00");await writer.drain()
            captured["request"] = await reader.readexactly(4)
            length = (await reader.readexactly(1))[0]
            captured["target"] = await reader.readexactly(length)
            captured["port"] = int.from_bytes(await reader.readexactly(2), "big")
            writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00");await writer.drain()
        finally:
            writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        await probe_proxy(parse_proxy(f"socks5://user:pass@127.0.0.1:{port}"), "vps.slsknet.org", 2271)
        assert captured["user"] == b"user" and captured["pass"] == b"pass"
        assert captured["target"] == b"vps.slsknet.org"
        assert captured["request"] == b"\x05\x01\x00\x03"
        assert captured["port"] == 2271
    finally:
        server.close();await server.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("reply,expected", [(b"\x05\xff", "PROXY_PROTOCOL_ERROR"), (b"\x05\x02", "PROXY_AUTH_REQUIRED")])
async def test_socks_probe_reports_protocol_and_missing_auth(reply, expected):
    async def handle(reader, writer):
        await reader.readexactly(3)
        writer.write(reply);await writer.drain();writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConnectionFailure) as error:
            await probe_proxy(parse_proxy(f"socks5://127.0.0.1:{port}"), "vps.slsknet.org", 2271)
        assert error.value.info["code"] == expected
    finally:
        server.close();await server.wait_closed()


def test_beets_transport_uses_shared_proxy_and_restores_library(monkeypatch):
    from musicbrainzngs import compat
    original = compat.build_opener
    original_socket = socket.socket
    calls = []
    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def request(self, method, url, **kwargs):
            return httpx.Response(200, content=b"metadata", request=httpx.Request(method, url))
    monkeypatch.setattr("app.proxy.httpx.Client", Client)
    with beets_proxy("socks5://host:1080"):
        assert compat.build_opener().open(Request("https://musicbrainz.org/ws/2/release/test")).read() == b"metadata"
        assert socket.socket is original_socket
    assert compat.build_opener is original
    assert calls[0]["proxy"] == "socks5://host:1080" and calls[0]["trust_env"] is False


def test_structured_error_does_not_log_out_manager(auth, monkeypatch):
    async def fail(*args, **kwargs):
        raise classify_connection_log("INVALIDPASS private-password")
    monkeypatch.setattr(auth.app.state.account, "login", fail)
    result = auth.post("/api/soulseek/auth/login", json={"username": "u", "password": "p"})
    assert result.status_code == 400
    assert result.json()["error"]["code"] == "INVALID_PASSWORD"
    assert "private-password" not in result.text
    assert auth.get("/api/session").status_code == 200


@pytest.mark.asyncio
async def test_proxy_auth_rejection_has_its_own_code():
    async def handle(reader, writer):
        await reader.readexactly(4)
        writer.write(b"\x05\x02");await writer.drain()
        _, length = await reader.readexactly(2)
        await reader.readexactly(length)
        length = (await reader.readexactly(1))[0]
        await reader.readexactly(length)
        writer.write(b"\x01\x01");await writer.drain();writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConnectionFailure) as error:
            await probe_proxy(parse_proxy(f"socks5://user:private-password@127.0.0.1:{port}"), "vps.slsknet.org", 2271)
        assert error.value.info["code"] == "PROXY_AUTH_FAILED"
        assert "private-password" not in str(error.value)
    finally:
        server.close();await server.wait_closed()


@pytest.mark.asyncio
async def test_reachable_proxy_can_still_refuse_soulseek_target():
    async def handle(reader, writer):
        await reader.readexactly(3)
        writer.write(b"\x05\x00");await writer.drain()
        await reader.readexactly(4)
        length = (await reader.readexactly(1))[0]
        await reader.readexactly(length+2)
        writer.write(b"\x05\x02\x00\x01");await writer.drain();writer.close()
    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(ConnectionFailure) as error:
            await probe_proxy(parse_proxy(f"socks5://127.0.0.1:{port}"), "vps.slsknet.org", 2271)
        assert error.value.info["code"] == "PROXY_TARGET_UNREACHABLE"
        assert "proxy rules" in error.value.detail
    finally:
        server.close();await server.wait_closed()


def test_musicbrainz_search_uses_explicit_shared_proxy(monkeypatch):
    from app.integrations import search_releases
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={"releases": []}, request=httpx.Request("GET", url))
    monkeypatch.setattr("app.integrations.httpx.get", get)
    monkeypatch.setattr("app.integrations.time.sleep", lambda _: None)
    assert search_releases("album", "socks5://proxy:1080") == []
    assert calls[0]["proxy"] == "socks5://proxy:1080" and calls[0]["trust_env"] is False


@pytest.mark.asyncio
async def test_internal_slskd_api_bypasses_outbound_proxy(settings, monkeypatch):
    from app.integrations import Soulseek
    settings.slskd_key = "k" * 32
    settings.outbound_proxy = "socks5://private-user:private-password@proxy:1080"
    calls = []
    original = httpx.AsyncClient
    def client(**kwargs):
        calls.append(kwargs)
        return original(**kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={})))
    monkeypatch.setattr("app.integrations.httpx.AsyncClient", client)
    assert await Soulseek(settings).request("GET", "/application") == {}
    assert calls[0]["trust_env"] is False and "proxy" not in calls[0]
