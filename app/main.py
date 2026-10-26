import asyncio
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr

from app.config import Settings
from app.accounts import SoulseekAccount
from app.errors import ConnectionFailure
from app.files import AUDIO, audio_metadata, list_directory, move, delete, operation_lock, safe_path
from app.integrations import Soulseek, search_releases, segment
from app.store import Store
from app.tagger import apply_import, preview, update_catalog_paths, match_album, remove_catalog_paths

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


class AlbumMatch(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    artist: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)


class SoulseekCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=255, repr=False)
    password: SecretStr = Field(min_length=1, max_length=512)
    password_confirm: SecretStr | None = None
    remember: bool = True


def create_app(settings=None):
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings.state)
    soulseek = Soulseek(settings)
    account = SoulseekAccount(settings, soulseek)
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
        restore_task = asyncio.create_task(account.maintain())
        try:
            yield
        finally:
            restore_task.cancel()
            with suppress(asyncio.CancelledError):
                await restore_task
            worker.shutdown(wait=True)

    app = FastAPI(title="meandmymusic", version="0.4.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.soulseek = settings, store, soulseek
    app.state.account = account

    @app.exception_handler(ConnectionFailure)
    async def connection_failure(request, error):
        return JSONResponse({"detail": error.detail, "error": error.info}, status_code=error.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        if request.url.path.startswith("/api/soulseek/auth/"):
            # FastAPI's default validation errors can echo entire request bodies.
            return JSONResponse({"detail": "Enter a Soulseek username and password (up to 512 characters)."}, status_code=422)
        return await request_validation_exception_handler(request, error)

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
                return JSONResponse({"detail": "Sign in to continue"}, status_code=401)
        if path.startswith("/api/") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("X-Requested-With") != "meandmymusic":
                return JSONResponse({"detail": "Missing request protection header"}, status_code=403)
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
                    store.update_job(plan_id, "failed", error="Import failed. Create a new preview.")
        worker.submit(execute)
        return {"id": job_id}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.4.0"}

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
            raise HTTPException(429, "Too many attempts. Wait a minute.")
        bucket.append(now)
        if not hmac.compare_digest(hashlib.sha256(data.password.encode()).digest(), hashlib.sha256(settings.password.encode()).digest()):
            raise HTTPException(401, "Incorrect password")
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
        return {"version": "0.4.0", "slskd": slskd_ok, "slskd_configured": bool(settings.slskd_key),
                "beets": True, "navidrome_url": settings.navidrome_url if settings.navidrome_url.startswith(("https://", "http://")) else "",
                "path_format": "$albumartist/$year - $album/$disc-$track $title", "import_mode": "copy"}

    @app.get("/api/soulseek/auth/status")
    async def soulseek_status():
        return await account.status()

    @app.post("/api/soulseek/auth/login")
    async def soulseek_login(data: SoulseekCredentials):
        username = data.username.strip()
        if not username or any(c in username for c in "\r\n\x00"):
            raise HTTPException(422, "Enter a valid Soulseek username")
        return await account.login(username, data.password.get_secret_value(), data.remember)

    @app.post("/api/soulseek/auth/register")
    async def soulseek_register(data: SoulseekCredentials):
        if data.password_confirm is None or not hmac.compare_digest(data.password.get_secret_value().encode(), data.password_confirm.get_secret_value().encode()):
            raise HTTPException(422, "Passwords do not match")
        # Soulseek registers an available name through the same login protocol.
        # The server does not distinguish first registration from an existing account.
        return await soulseek_login(data)

    @app.post("/api/soulseek/auth/reconnect")
    async def soulseek_reconnect():
        return await account.reconnect()

    @app.post("/api/soulseek/auth/logout")
    async def soulseek_logout():
        return await account.logout()

    @app.delete("/api/soulseek/auth/saved")
    async def soulseek_forget():
        account.restore_enabled = False
        account.vault.forget()
        return {"saved": False, "message": "Saved account removed. Use Sign out to disconnect the active session."}

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
            raise HTTPException(400, "Select an audio file")
        return audio_metadata(target)

    @app.get("/api/media")
    def media(root: Root, path: str):
        target = safe_path(settings.library if root == "library" else settings.inbox, path)
        if not target.is_file() or target.suffix.lower() not in AUDIO | {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(400, "This file format cannot be previewed")
        return FileResponse(target)

    @app.post("/api/folders")
    def create_folder(data: Location):
        with operation_lock:
            target = safe_path(root(data.root), data.path, exists=False, allow_root=False)
            if not target.parent.is_dir():
                raise HTTPException(400, "Parent folder does not exist")
            try:
                target.mkdir()
            except FileExistsError:
                raise HTTPException(409, "Folder already exists")
            except PermissionError:
                raise HTTPException(403, "Permission denied: cannot write to this folder")
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
                    raise HTTPException(500, "Could not update the beets catalog. Move rolled back.")
            store.event("move", {"root": data.root, **result})
            return {"ok": True}

    @app.delete("/api/files")
    def delete_file(data: Location):
        with operation_lock:
            result = delete(root(data.root), data.path)
            if data.root == "library":
                remove_catalog_paths(settings, settings.library / data.path)
            store.event("delete", {"root": data.root, **result})
            return {"ok": True}

    @app.get("/api/history")
    def history():
        return store.events()

    @app.post("/api/history/{event_id}/undo")
    def undo(event_id: UUID):
        with operation_lock:
            event = next((e for e in store.events() if e["id"] == str(event_id)), None)
            if not event or event["undone"]:
                raise HTTPException(409, "This operation cannot be undone")
            data = event["data"]
            if event["kind"] == "move":
                # Only undo a move if the destination has not been changed by a later
                # manager operation. Existing source names are never overwritten.
                later = [e for e in store.events() if e["created"] > event["created"] and not e["undone"]]
                if any(e["kind"] == "move" and e["data"]["root"] == data["root"] and
                       (e["data"]["source"] == data["destination"] or e["data"]["source"].startswith(data["destination"] + "/")) for e in later):
                    raise HTTPException(409, "Undo subsequent moves first")
                move(root(data["root"]), data["destination"], data["source"])
                if data["root"] == "library":
                    try:
                        update_catalog_paths(settings, settings.library / data["destination"], settings.library / data["source"])
                    except Exception:
                        move(root(data["root"]), data["source"], data["destination"])
                        raise HTTPException(500, "Could not update the catalog. Undo failed.")
            elif event["kind"] == "mkdir":
                try:
                    safe_path(root(data["root"]), data["path"], allow_root=False).rmdir()
                except OSError:
                    raise HTTPException(409, "Only an empty folder can be removed by undo")
            else:
                raise HTTPException(400, "Import cannot be undone automatically. Original files remain in Inbox.")
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
            raise HTTPException(400, "Enter between 2 and 300 characters")
        return search_releases(query.strip(), settings.outbound_proxy)

    @app.post("/api/imports/preview")
    def preview_import(data: Preview):
        safe_path(settings.inbox, data.path, allow_root=False)
        return run_job("preview", preview, data.path, str(data.release_id))

    @app.post("/api/musicbrainz/match")
    def match_metadata(data: AlbumMatch):
        try:
            return match_album(settings, data.path, data.artist, data.album)
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "Could not match a MusicBrainz release. Check the tags, connection, artist and album.") from None

    @app.post("/api/imports/{plan_id}/apply")
    def import_album(plan_id: UUID):
        plan = store.get_job(str(plan_id))
        if not plan or plan["status"] != "ready" or plan["kind"] != "preview" or not plan["result"]:
            raise HTTPException(409, "Create a new preview first")
        if not store.claim_plan(str(plan_id)):
            raise HTTPException(409, "This preview is already in use")
        return run_job("import", apply_import, plan["result"], plan_id=str(plan_id))

    @app.get("/api/jobs")
    def jobs():
        return store.jobs()

    @app.get("/api/jobs/{job_id}")
    def job(job_id: UUID):
        result = store.get_job(str(job_id))
        if not result:
            raise HTTPException(404, "Task not found")
        return result

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
