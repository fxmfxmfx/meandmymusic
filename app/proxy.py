"""Explicit outbound proxy routing; internal container traffic is never proxied."""
import asyncio
from contextlib import contextmanager, suppress
from dataclasses import dataclass
import io
import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit

import httpx

from app.errors import ConnectionFailure


@dataclass(repr=False, frozen=True)
class OutboundProxy:
    url: str
    host: str
    port: int
    username: str | None = None
    password: str | None = None

    def status(self):
        return {"enabled": True, "scheme": "socks5", "host": self.host, "port": self.port, "authenticated": self.username is not None}

    def slskd_options(self):
        return {"enabled": True, "address": self.host, "port": self.port, "username": self.username, "password": self.password}


def parse_proxy(value):
    if not value:
        return None
    try:
        url = urlsplit(value)
        if url.scheme not in {"socks5", "socks5h"} or not url.hostname or not url.port:
            raise ValueError
        if url.path not in {"", "/"} or url.query or url.fragment:
            raise ValueError
        username = unquote(url.username) if url.username is not None else None
        password = unquote(url.password) if url.password is not None else None
        if username is not None and (not username or password is None or not password):
            raise ValueError
        if any(len(v.encode()) > 255 for v in (username, password) if v is not None):
            raise ValueError
        # HTTPX 0.28 and slskd both support SOCKS5 with remote target DNS.
        return OutboundProxy(value.rstrip("/"), url.hostname, url.port, username, password)
    except (ValueError, UnicodeError):
        raise ConnectionFailure("PROXY_CONFIG_INVALID", "Invalid OUTBOUND_PROXY.", stage="proxy", hint="Use socks5://HOST:PORT or socks5://USER:PASSWORD@HOST:PORT. Percent-encode special characters in credentials. Soulseek requires a SOCKS5 proxy.", status=400) from None


def proxy_status(value):
    try:
        proxy = parse_proxy(value)
        return proxy.status() if proxy else {"enabled": False}
    except ConnectionFailure as error:
        return {"enabled": bool(value), "error": error.info}


async def probe_proxy(proxy, host, port, timeout=7):
    """Test the same SOCKS5 TCP route as slskd, without sending Soulseek credentials."""
    writer = None
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await asyncio.open_connection(proxy.host, proxy.port)
            writer.write(b"\x05\x02\x00\x02" if proxy.username is not None else b"\x05\x01\x00")
            await writer.drain()
            version, method = await reader.readexactly(2)
            if version != 5 or method == 255 or method not in (0, 2):
                raise ConnectionFailure("PROXY_PROTOCOL_ERROR", "The proxy rejected the SOCKS5 connection.", stage="proxy", hint="Check that this port provides a SOCKS5 proxy.")
            if method == 2:
                if proxy.username is None:
                    raise ConnectionFailure("PROXY_AUTH_REQUIRED", "The proxy requires a username and password.", stage="proxy", hint="Add USER:PASSWORD to OUTBOUND_PROXY.")
                username, password = proxy.username.encode(), proxy.password.encode()
                writer.write(b"\x01" + bytes([len(username)]) + username + bytes([len(password)]) + password)
                await writer.drain()
                auth_version, result = await reader.readexactly(2)
                if auth_version != 1 or result != 0:
                    raise ConnectionFailure("PROXY_AUTH_FAILED", "The proxy rejected the username or password.", stage="proxy", hint="Check the proxy credentials in OUTBOUND_PROXY.")
            try:
                ip = ipaddress.ip_address(host)
                target = bytes([1 if ip.version == 4 else 4]) + ip.packed
            except ValueError:
                encoded = host.encode("idna")
                if not 1 <= len(encoded) <= 255:
                    raise ConnectionFailure("SERVER_ADDRESS_INVALID", "Invalid Soulseek server address.", stage="network")
                target = b"\x03" + bytes([len(encoded)]) + encoded
            writer.write(b"\x05\x01\x00" + target + int(port).to_bytes(2, "big"))
            await writer.drain()
            version, reply, _, address_type = await reader.readexactly(4)
            if version != 5:
                raise ConnectionFailure("PROXY_PROTOCOL_ERROR", "The proxy returned an invalid SOCKS5 response.", stage="proxy")
            if reply:
                reasons = {1: "internal proxy error", 2: "connection blocked by proxy rules", 3: "network unreachable", 4: "host unreachable or DNS resolution failed", 5: "connection refused by host", 6: "route timed out", 7: "CONNECT command not supported", 8: "address type not supported"}
                raise ConnectionFailure("PROXY_TARGET_UNREACHABLE", "The proxy could not connect to Soulseek: " + reasons.get(reply, "unknown failure") + ".", stage="proxy", hint="The SOCKS5 server is reachable, but the route to Soulseek failed.")
            length = 4 if address_type == 1 else 16 if address_type == 4 else (await reader.readexactly(1))[0] if address_type == 3 else 0
            if not length:
                raise ConnectionFailure("PROXY_PROTOCOL_ERROR", "Invalid address in SOCKS5 response.", stage="proxy")
            await reader.readexactly(length + 2)
    except ConnectionFailure:
        raise
    except socket.gaierror:
        raise ConnectionFailure("PROXY_DNS_FAILED", "Could not resolve the proxy hostname.", stage="proxy", hint="Check the hostname in OUTBOUND_PROXY and container DNS.") from None
    except ConnectionRefusedError:
        raise ConnectionFailure("PROXY_REFUSED", "Connection to the proxy was refused.", stage="proxy", hint="Check the address and port. For a proxy on the Docker host, use host.docker.internal and a Docker-accessible listening address.") from None
    except TimeoutError:
        raise ConnectionFailure("PROXY_TIMEOUT", "The proxy timed out.", stage="proxy", hint="Check that containers can reach the proxy.", status=504) from None
    except (OSError, asyncio.IncompleteReadError, ValueError):
        raise ConnectionFailure("PROXY_CONNECTION_FAILED", "The SOCKS5 proxy connection was interrupted.", stage="proxy", hint="Check the protocol, address and network rules.") from None
    finally:
        if writer:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


@contextmanager
def beets_proxy(value):
    """Adapt the pinned musicbrainzngs transport only; never patch global sockets."""
    proxy = parse_proxy(value)
    if proxy is None:
        yield
        return
    from musicbrainzngs import compat
    original = compat.build_opener

    class Opener:
        def open(self, request, body=None):
            try:
                with httpx.Client(proxy=proxy.url, trust_env=False, follow_redirects=True, timeout=25) as client:
                    response = client.request(request.get_method(), request.full_url, headers=dict(request.header_items()), content=body or request.data)
                if response.is_error:
                    raise HTTPError(request.full_url, response.status_code, "MusicBrainz HTTP error", response.headers, io.BytesIO(response.content))
                return io.BytesIO(response.content)
            except httpx.HTTPError:
                raise URLError("MusicBrainz connection through OUTBOUND_PROXY failed") from None

    compat.build_opener = lambda *handlers: Opener()
    try:
        yield
    finally:
        compat.build_opener = original
