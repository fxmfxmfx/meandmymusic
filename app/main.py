import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import Settings
from app.files import AUDIO, audio_metadata, list_directory, move, operation_lock, safe_path
from app.integrations import Soulseek, search_releases, segment
from app.store import Store
from app.tagger import apply_import, preview, update_catalog_paths

STATIC = Path(__file__).parent / "static"
Root = Literal["library", "inbox"]


class Login(BaseModel):
    password: str = Field(max_length=512)


class Location(BaseModel):
    root: Root
    path: str = Field(max_length=4096)


class Move(BaseModel):
    root: Root
    source: str = Field(max_length=4096)
    destination: str = Field(max_length=4096)


class Search(BaseModel):
    query: str = Field(min_length=2, max_length=300)


class DownloadFile(BaseModel):
    filename: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0)


class Download(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    files: list[DownloadFile] = Field(min_length=1, max_length=500)


class Preview(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    release_id: UUID


def create_app(settings=None):
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings.state)
    soulseek = Soulseek(settings)
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="beets")
    secret_path = settings.state / "session.key"
    try:
        with secret_path.open("x") as file:
            os.chmod(secret_path, 0o600)
            file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    secret = hashlib.sha256((secret_path.read_text() + settings.password).encode()).digest()
    attempts = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        yield
        worker.shutdown(wait=True)

    app = FastAPI(title="meandmymusic", version="0.1.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.soulseek = settings, store, soulseek

    def sign(value):
        return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()

    def valid_session(value):
        try:
            payload, signature = value.rsplit(".", 1)
            return hmac.compare_digest(sign(payload), signature) and time.time() < int(payload.split(".", 1)[0])
        except (ValueError, AttributeError):
            return False

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in {"/api/health", "/api/login"}:
            if not valid_session(request.cookies.get("mmm_session", "")):
                return JSONResponse({"detail": "Войдите в свой архив"}, status_code=401)
        if path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("X-Requested-With") != "meandmymusic":
                return JSONResponse({"detail": "Отсутствует заголовок защиты запроса"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def root(name):
        return settings.library if name == "library" else settings.inbox

    def run_job(kind, function, *args, plan_id=None):
        job_id = store.new_job(kind)
        def execute():
            store.update_job(job_id, "running")
            try:
                result = function(settings, *args)
                store.update_job(job_id, "ready" if kind == "preview" else "done", result=result)
                if kind == "import":
                    store.update_job(plan_id, "applied")
                    store.event("import", result)
            except Exception as error:
                message = error.detail if isinstance(error, HTTPException) else str(error)
                # Never persist connection credentials in errors.
                for value in (settings.password, settings.slskd_key):
                    if value:
                        message = message.replace(value, "[redacted]")
                store.update_job(job_id, "failed", error=message[:2000])
                if plan_id:
                    store.update_job(plan_id, "failed", error="Импорт не выполнен. Создайте новый предпросмотр.")
        worker.submit(execute)
        return {"id": job_id}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0"}

    @app.post("/api/login")
    def login(data: Login, request: Request, response: Response):
        ip = request.client.host if request.client else "unknown"
        now = time.time()
        if len(attempts) > 1000:
            for key in list(attempts):
                if not attempts[key] or attempts[key][-1] < now - 60:
                    del attempts[key]
        bucket = attempts[ip]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 8:
            raise HTTPException(429, "Слишком много попыток. Подождите минуту.")
        bucket.append(now)
        if not hmac.compare_digest(hashlib.sha256(data.password.encode()).digest(), hashlib.sha256(settings.password.encode()).digest()):
            raise HTTPException(401, "Неверный пароль")
        payload = f"{int(now + 43200)}.{secrets.token_hex(16)}"
        response.set_cookie("mmm_session", payload + "." + sign(payload), max_age=43200, httponly=True,
                            secure=settings.secure_cookie, samesite="strict")
        return {"ok": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie("mmm_session")
        return {"ok": True}

    @app.get("/api/system")
    async def system():
        slskd_ok = False
        if settings.slskd_key:
            try:
                await soulseek.request("GET", "/application")
                slskd_ok = True
            except HTTPException:
                pass
        return {"version": "0.1.0", "slskd": slskd_ok, "slskd_configured": bool(settings.slskd_key),
                "beets": True, "navidrome_url": settings.navidrome_url if settings.navidrome_url.startswith(("https://", "http://")) else "",
                "path_format": "$albumartist/$year - $album/$disc-$track $title", "import_mode": "copy"}

    @app.get("/api/session")
    def session():
        return {"authenticated": True}

    @app.get("/api/files")
    def files(root: Root = "library", path: str = ""):
        return list_directory(settings.library if root == "library" else settings.inbox, path)

    @app.get("/api/metadata")
    def metadata(root: Root, path: str):
        target = safe_path(settings.library if root == "library" else settings.inbox, path)
        if not target.is_file() or target.suffix.lower() not in AUDIO:
            raise HTTPException(400, "Выберите аудиофайл")
        return audio_metadata(target)

    @app.get("/api/media")
    def media(root: Root, path: str):
        target = safe_path(settings.library if root == "library" else settings.inbox, path)
        if not target.is_file() or target.suffix.lower() not in AUDIO | {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(400, "Этот формат недоступен для просмотра")
        return FileResponse(target)

    @app.post("/api/folders")
    def create_folder(data: Location):
        with operation_lock:
            target = safe_path(root(data.root), data.path, exists=False, allow_root=False)
            if not target.parent.is_dir():
                raise HTTPException(400, "Родительская папка не существует")
            try:
                target.mkdir()
            except FileExistsError:
                raise HTTPException(409, "Папка уже существует")
            except PermissionError:
                raise HTTPException(403, "Нет прав на запись в папку")
            store.event("mkdir", data.model_dump())
            return {"ok": True}

    @app.post("/api/move")
    def move_file(data: Move):
        with operation_lock:
            result = move(root(data.root), data.source, data.destination)
            if data.root == "library":
                try:
                    update_catalog_paths(settings, settings.library / data.source, settings.library / data.destination)
                except Exception:
                    move(root(data.root), data.destination, data.source)
                    raise HTTPException(500, "Каталог beets не обновился. Перемещение отменено.")
            store.event("move", {"root": data.root, **result})
            return {"ok": True}

    @app.get("/api/history")
    def history():
        return store.events()

    @app.post("/api/history/{event_id}/undo")
    def undo(event_id: UUID):
        with operation_lock:
            event = next((e for e in store.events() if e["id"] == str(event_id)), None)
            if not event or event["undone"]:
                raise HTTPException(409, "Операция недоступна для отмены")
            data = event["data"]
            if event["kind"] == "move":
                # Only undo a move if the destination has not been changed by a later
                # manager operation. Existing source names are never overwritten.
                later = [e for e in store.events() if e["created"] > event["created"] and not e["undone"]]
                if any(e["kind"] == "move" and e["data"]["root"] == data["root"] and
                       (e["data"]["source"] == data["destination"] or e["data"]["source"].startswith(data["destination"] + "/")) for e in later):
                    raise HTTPException(409, "Сначала отмените более поздние перемещения")
                move(root(data["root"]), data["destination"], data["source"])
                if data["root"] == "library":
                    try:
                        update_catalog_paths(settings, settings.library / data["destination"], settings.library / data["source"])
                    except Exception:
                        move(root(data["root"]), data["source"], data["destination"])
                        raise HTTPException(500, "Не удалось обновить каталог. Отмена не выполнена.")
            elif event["kind"] == "mkdir":
                try:
                    safe_path(root(data["root"]), data["path"], allow_root=False).rmdir()
                except OSError:
                    raise HTTPException(409, "Можно отменить создание только пустой папки")
            else:
                raise HTTPException(400, "Для импорта автоматическая отмена пока недоступна. Оригиналы сохранены во входящих.")
            with store.connect() as db:
                db.execute("UPDATE events SET undone=1 WHERE id=?", (str(event_id),))
            return {"ok": True}

    @app.post("/api/soulseek/searches")
    async def search(data: Search):
        return await soulseek.request("POST", "/searches", {"searchText": data.query.strip(), "fileLimit": 3000, "responseLimit": 100})

    @app.get("/api/soulseek/searches/{search_id}")
    async def search_results(search_id: UUID):
        status, responses = await asyncio.gather(soulseek.request("GET", f"/searches/{search_id}"),
                                                 soulseek.request("GET", f"/searches/{search_id}/responses"))
        return {"search": status, "responses": responses}

    @app.delete("/api/soulseek/searches/{search_id}")
    async def stop_search(search_id: UUID):
        return await soulseek.request("DELETE", f"/searches/{search_id}")

    @app.post("/api/soulseek/downloads")
    async def download(data: Download):
        result = await soulseek.request("POST", f"/transfers/downloads/{segment(data.username)}", [file.model_dump() for file in data.files])
        store.event("download", {"username": data.username, "files": len(data.files)})
        return {"ok": True, "result": result}

    @app.get("/api/soulseek/downloads")
    async def downloads():
        return await soulseek.request("GET", "/transfers/downloads")

    @app.delete("/api/soulseek/downloads/{username}/{transfer_id}")
    async def cancel_download(username: str, transfer_id: UUID):
        return await soulseek.request("DELETE", f"/transfers/downloads/{segment(username)}/{transfer_id}?remove=false")

    @app.get("/api/musicbrainz/releases")
    def releases(query: str):
        if len(query.strip()) < 2 or len(query) > 300:
            raise HTTPException(400, "Введите от 2 до 300 символов")
        return search_releases(query.strip())

    @app.post("/api/imports/preview")
    def preview_import(data: Preview):
        safe_path(settings.inbox, data.path, allow_root=False)
        return run_job("preview", preview, data.path, str(data.release_id))

    @app.post("/api/imports/{plan_id}/apply")
    def import_album(plan_id: UUID):
        plan = store.get_job(str(plan_id))
        if not plan or plan["status"] != "ready" or plan["kind"] != "preview" or not plan["result"]:
            raise HTTPException(409, "Сначала создайте новый предпросмотр")
        if not store.claim_plan(str(plan_id)):
            raise HTTPException(409, "Этот предпросмотр уже используется")
        return run_job("import", apply_import, plan["result"], plan_id=str(plan_id))

    @app.get("/api/jobs")
    def jobs():
        return store.jobs()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: UUID):
        result = store.get_job(str(job_id))
        if not result:
            raise HTTPException(404, "Задание не найдено")
        return result

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
