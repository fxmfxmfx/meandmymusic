import threading
import time
from urllib.parse import quote

import httpx
from fastapi import HTTPException
from app.errors import ConnectionFailure
from app.proxy import parse_proxy

musicbrainz_lock = threading.Lock()


class Soulseek:
    def __init__(self, settings):
        self.settings = settings

    async def request(self, method, path, data=None, *, bearer=None, timeout=8):
        if not self.settings.slskd_key:
            raise HTTPException(503, "Configure SLSKD_URL and SLSKD_API_KEY on the server")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = await client.request(method, self.settings.slskd_url + "/api/v0" + path,
                                                headers={"Authorization": "Bearer " + bearer} if bearer else {"X-API-Key": self.settings.slskd_key}, json=data)
            if response.is_error:
                hints = {401: "The slskd API key does not match SLSKD_API_KEY.", 403: "Check that the key has the Administrator role and remote configuration is enabled.", 404: "Check SLSKD_URL and the pinned slskd version.", 400: "slskd rejected the parameters. Check its configuration."}
                raise ConnectionFailure("SLSKD_HTTP_" + str(response.status_code), f"slskd API returned HTTP {response.status_code}.", stage="slskd_api", hint=hints.get(response.status_code, "Check the slskd status and logs."))
            return response.json() if response.content else {}
        except httpx.TimeoutException:
            raise ConnectionFailure("SLSKD_API_TIMEOUT", "The slskd API timed out.", stage="slskd_api", hint="Check docker compose ps and slskd logs. This internal connection bypasses OUTBOUND_PROXY.", status=504) from None
        except (httpx.HTTPError, ValueError):
            raise ConnectionFailure("SLSKD_UNREACHABLE", "The manager cannot reach the slskd API.", stage="slskd_api", hint="Check that slskd is running and SLSKD_URL points to it.") from None

    async def admin_token(self):
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
                response = await client.post(self.settings.slskd_url + "/api/v0/session", json={
                    "username": self.settings.slskd_admin_user,
                    "password": self.settings.slskd_admin_password or self.settings.password})
            if response.is_error:
                raise ConnectionFailure("SLSKD_ADMIN_AUTH_FAILED", "slskd rejected the administrative account.", stage="slskd_api", hint="Compose uses the archive user and APP_PASSWORD. External slskd instances require SLSKD_ADMIN_USER/SLSKD_ADMIN_PASSWORD.")
            token = response.json().get("token")
            if not isinstance(token, str) or not token:
                raise ValueError
            return token
        except (httpx.HTTPError, ValueError, AttributeError):
            raise ConnectionFailure("SLSKD_ADMIN_UNREACHABLE", "The slskd administrative API is unavailable.", stage="slskd_api", hint="Check slskd availability and SLSKD_URL.") from None


def search_releases(query, outbound_proxy=""):
    # Shared with the beets matcher: one MusicBrainz request at a time.
    with musicbrainz_lock:
        try:
            proxy = parse_proxy(outbound_proxy)
            response = httpx.get("https://musicbrainz.org/ws/2/release/",
                                 params={"query": query, "fmt": "json", "limit": 15},
                                 headers={"User-Agent": "meandmymusic/0.4 (https://github.com/fxmfxmfx/meandmymusic)"}, timeout=25, proxy=proxy.url if proxy else None, trust_env=False)
            response.raise_for_status()
            return [{"id": r["id"], "title": r.get("title", ""), "date": r.get("date", ""),
                     "artist": "".join(a.get("name", a.get("artist", {}).get("name", "")) + a.get("joinphrase", "") for a in r.get("artist-credit", []) if isinstance(a, dict)),
                     "country": r.get("country", ""), "status": r.get("status", ""),
                     "tracks": r.get("track-count", 0), "score": r.get("score", 0),
                     "format": ", ".join(m.get("format", "") for m in r.get("media", [])),
                     "disambiguation": r.get("disambiguation", "")}
                    for r in response.json().get("releases", [])]
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(502, "MusicBrainz is unavailable. Try again later.") from error
        finally:
            time.sleep(1.1)


def segment(value):
    return quote(value, safe="")
