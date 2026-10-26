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
            raise HTTPException(503, "Укажите SLSKD_URL и SLSKD_API_KEY в настройках сервера")
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
                response = await client.request(method, self.settings.slskd_url + "/api/v0" + path,
                                                headers={"Authorization": "Bearer " + bearer} if bearer else {"X-API-Key": self.settings.slskd_key}, json=data)
            if response.is_error:
                hints = {401: "API-ключ slskd не совпадает с SLSKD_API_KEY менеджера.", 403: "Проверь административную роль ключа slskd и включённую удалённую конфигурацию.", 404: "Проверь SLSKD_URL и закреплённую версию slskd.", 400: "slskd отклонил параметры. Проверь его конфигурацию."}
                raise ConnectionFailure("SLSKD_HTTP_" + str(response.status_code), f"API slskd вернул HTTP {response.status_code}.", stage="slskd_api", hint=hints.get(response.status_code, "Проверь состояние и логи slskd."))
            return response.json() if response.content else {}
        except httpx.TimeoutException:
            raise ConnectionFailure("SLSKD_API_TIMEOUT", "API slskd не ответил вовремя.", stage="slskd_api", hint="Проверь docker compose ps и логи slskd. Это внутреннее подключение, оно не использует OUTBOUND_PROXY.", status=504) from None
        except (httpx.HTTPError, ValueError):
            raise ConnectionFailure("SLSKD_UNREACHABLE", "Менеджер не может подключиться к API slskd.", stage="slskd_api", hint="Проверь, что контейнер slskd запущен и SLSKD_URL указывает на него.") from None

    async def admin_token(self):
        try:
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
                response = await client.post(self.settings.slskd_url + "/api/v0/session", json={
                    "username": self.settings.slskd_admin_user,
                    "password": self.settings.slskd_admin_password or self.settings.password})
            if response.is_error:
                raise ConnectionFailure("SLSKD_ADMIN_AUTH_FAILED", "slskd отклонил административную веб-учётку.", stage="slskd_api", hint="В штатном Compose это пользователь archive и APP_PASSWORD. Для внешнего slskd задай SLSKD_ADMIN_USER/SLSKD_ADMIN_PASSWORD.")
            token = response.json().get("token")
            if not isinstance(token, str) or not token:
                raise ValueError
            return token
        except (httpx.HTTPError, ValueError, AttributeError):
            raise ConnectionFailure("SLSKD_ADMIN_UNREACHABLE", "Административный API slskd недоступен.", stage="slskd_api", hint="Проверь доступность контейнера slskd и SLSKD_URL.") from None


def search_releases(query, outbound_proxy=""):
    # Shared with the beets matcher: one MusicBrainz request at a time.
    with musicbrainz_lock:
        try:
            proxy = parse_proxy(outbound_proxy)
            response = httpx.get("https://musicbrainz.org/ws/2/release/",
                                 params={"query": query, "fmt": "json", "limit": 15},
                                 headers={"User-Agent": "meandmymusic/0.3 (https://github.com/fxmfxmfx/meandmymusic)"}, timeout=25, proxy=proxy.url if proxy else None, trust_env=False)
            response.raise_for_status()
            return [{"id": r["id"], "title": r.get("title", ""), "date": r.get("date", ""),
                     "artist": "".join(a.get("name", a.get("artist", {}).get("name", "")) + a.get("joinphrase", "") for a in r.get("artist-credit", []) if isinstance(a, dict)),
                     "country": r.get("country", ""), "status": r.get("status", ""),
                     "tracks": r.get("track-count", 0), "score": r.get("score", 0),
                     "format": ", ".join(m.get("format", "") for m in r.get("media", [])),
                     "disambiguation": r.get("disambiguation", "")}
                    for r in response.json().get("releases", [])]
        except (httpx.HTTPError, ValueError) as error:
            raise HTTPException(502, "MusicBrainz недоступен. Попробуйте позже.") from error
        finally:
            time.sleep(1.1)


def segment(value):
    return quote(value, safe="")
