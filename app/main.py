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

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, StrictBool

from app.config import Settings
from app.accounts import SoulseekAccount
from app.errors import ConnectionFailure
from app.files import AUDIO, audio_metadata, list_directory, search_library, move, delete, operation_lock, safe_path
from app.integrations import Soulseek, search_releases, segment
from app.store import Store
from app.uploads import Uploads
from app.library_stats import LibraryStats
from app.manual_import import preview_manual, apply_manual
from app.tagger import apply_import, preview, update_catalog_paths, match_album, remove_catalog_paths, update_inbox_paths
from app.inbox import annotate as annotate_inbox, snapshot, album_paths, imported_sources

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
    query: str = Field(max_length=300)
    mode: Literal["standard", "extended"] = "standard"


class DownloadFile(BaseModel):
    filename: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0)


class Download(BaseModel):
    username: str = Field(min_length=1, max_length=200)
    files: list[DownloadFile] = Field(min_length=1, max_length=500)


class Preview(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    release_id: UUID
    keep_source: StrictBool = False


class AlbumMatch(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    artist: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)


class ManualImport(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    destination: str = Field(min_length=1, max_length=4096)


class MatchAll(BaseModel):
    path: str = Field(default='', max_length=4096)


class UploadFileSpec(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    size: int = Field(ge=0, le=50 * 1024**3)


class UploadManifest(BaseModel):
    root: Root
    path: str = Field(default='', max_length=4096)
    files: list[UploadFileSpec] = Field(default_factory=list, max_length=10000)
    directories: list[str] = Field(default_factory=list, max_length=10000)


class Preferences(BaseModel):
    delete_inbox_after_import: StrictBool


class SoulseekCredentials(BaseModel):
    username: str = Field(min_length=1, max_length=255, repr=False)
    password: SecretStr = Field(min_length=1, max_length=512)
    password_confirm: SecretStr | None = None
    remember: bool = True


def create_app(settings=None):
    settings = settings or Settings()
    settings.prepare()
    store = Store(settings.state)
    uploads = Uploads(settings)
    library_stats = LibraryStats(settings)
    soulseek = Soulseek(settings)
    download_lock = asyncio.Lock()
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
            uploads.close()
            library_stats.close()

    app = FastAPI(title="meandmymusic", version="0.8.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings, app.state.store, app.state.soulseek = settings, store, soulseek
    app.state.account = account
    app.state.library_stats = library_stats

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

    def prepare_preview(settings, source, release_id, keep_source=False):
        delete_source = store.preferences()['delete_inbox_after_import'] and not keep_source
        digest, _ = snapshot(settings, source)
        result = preview(settings, source, release_id)
        result['delete_source'] = delete_source
        if keep_source:
            result['keep_source'] = True
        if digest != snapshot(settings, source)[0]:
            raise HTTPException(409, 'Inbox folder changed while preparing the preview. Try again.')
        old = store.matches().get(source)
        match = dict(old['result']) if old and old['signature'] == digest else {
            'artist': result.get('artist', ''), 'album': result.get('album', ''),
            'tracks': len(result['rows']), 'has_tags': True, 'candidates': [], 'warnings': []}
        match['selected_release_id'] = release_id
        result['match_signature'], result['match_result'] = digest, match
        return result

    def prepare_manual(settings, source, destination):
        delete_source = store.preferences()['delete_inbox_after_import']
        result = preview_manual(settings, source, destination)
        result['delete_source'] = delete_source
        return result

    def perform_match(settings, source, artist=None, album=None):
        digest, _ = snapshot(settings, source)
        result = match_album(settings, source, artist, album)
        if digest != snapshot(settings, source)[0]:
            raise HTTPException(409, 'Inbox folder changed while matching. Try again.')
        return digest, result

    def prepare_match(settings, source):
        digest, result = perform_match(settings, source)
        plan, preview_error = None, None
        if result.get('recommended_release_id'):
            try:
                plan = prepare_preview(settings, source, result['recommended_release_id'])
            except ValueError as error:
                preview_error = str(error)
        if digest != snapshot(settings, source)[0]:
            raise HTTPException(409, 'Inbox folder changed while matching. Try again.')
        return {'source': source, 'signature': digest, 'match': result, 'plan': plan, 'preview_error': preview_error}

    def run_job(kind, function, *args, plan_id=None, job_source=None):
        job_id = store.new_job(kind, job_source)
        def execute():
            if not store.start_job(job_id):
                return
            try:
                result = function(settings, *args)
                record = (job_source, result['signature'], result['match']) if kind == 'match' else (
                    (job_source, result['match_signature'], result['match_result']) if kind == 'preview' and result.get('match_result') else None)
                store.finish_job(job_id, "ready" if kind in {'preview', 'match'} else "done", result=result, match_record=record)
                if kind == "import":
                    store.update_job(plan_id, "applied")
                    store.event("import", result)
            except Exception as error:
                message = error.detail if isinstance(error, HTTPException) else str(error)
                # Never persist connection credentials in errors.
                for value in (settings.password, settings.slskd_key):
                    if value:
                        message = message.replace(value, "[redacted]")
                store.finish_job(job_id, "failed", error=message[:2000])
                if plan_id:
                    store.update_job(plan_id, "failed", error="Import failed. Create a new preview.")
        worker.submit(execute)
        return {"id": job_id}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.8.0"}

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
        return {"version": "0.8.0", "slskd": slskd_ok, "slskd_configured": bool(settings.slskd_key),
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

    @app.get('/api/preferences')
    def preferences():
        return store.preferences()

    @app.patch('/api/preferences')
    def set_preferences(data: Preferences):
        return store.set_preferences(data.model_dump())

    @app.get('/api/library/stats')
    def library_statistics():
        return library_stats.get()

    @app.get('/api/library/search')
    def library_search(q: str = Query(min_length=1, max_length=300),
                       offset: int = Query(default=0, ge=0, le=1_000_000),
                       limit: int = Query(default=200, ge=1, le=500)):
        return search_library(settings.library, q, offset=offset, limit=limit)

    @app.post('/api/library/stats/refresh')
    def refresh_library_statistics():
        return library_stats.get(refresh=True)

    @app.get("/api/files")
    def files(root: Root = "library", path: str = ""):
        with operation_lock:
            listing = list_directory(settings.library if root == "library" else settings.inbox, path)
            return annotate_inbox(settings, listing, store) if root == 'inbox' else listing

    @app.post('/api/uploads')
    def begin_upload(data: UploadManifest):
        if not data.files and not data.directories:
            raise HTTPException(400, 'Choose files or a folder to upload')
        return uploads.begin(data.root, data.path, [f.model_dump() for f in data.files], data.directories)

    @app.put('/api/uploads/{upload_id}/{index}')
    async def receive_upload(upload_id: UUID, index: int, request: Request):
        return await uploads.receive(str(upload_id), index, request)

    @app.post('/api/uploads/{upload_id}/complete')
    def complete_upload(upload_id: UUID):
        result = uploads.complete(str(upload_id))
        store.event('upload', result)
        return result

    @app.delete('/api/uploads/{upload_id}')
    def cancel_upload(upload_id: UUID):
        uploads.discard(str(upload_id))
        return {'ok': True}

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
            else:
                try:
                    update_inbox_paths(settings, data.source, data.destination)
                    store.move_matches(data.source, data.destination)
                except Exception:
                    move(settings.inbox, data.destination, data.source)
                    update_inbox_paths(settings, data.destination, data.source)
                    raise HTTPException(500, 'Could not update Inbox status. Move rolled back.') from None
            store.event("move", {"root": data.root, **result})
            return {"ok": True}

    @app.delete("/api/files")
    def delete_file(data: Location):
        with operation_lock:
            result = delete(root(data.root), data.path)
            if data.root == "library":
                remove_catalog_paths(settings, settings.library / data.path)
            else:
                update_inbox_paths(settings, data.path, None)
                store.delete_matches(data.path)
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
                else:
                    try:
                        update_inbox_paths(settings, data['destination'], data['source'])
                        store.move_matches(data['destination'], data['source'])
                    except Exception:
                        move(settings.inbox, data['source'], data['destination'])
                        update_inbox_paths(settings, data['source'], data['destination'])
                        raise HTTPException(500, 'Could not update Inbox status. Undo failed.') from None
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
        query = data.query.strip()
        if len(query) < 2:
            raise HTTPException(400, "Search query must contain at least 2 characters")
        payload = {"searchText": query, "fileLimit": 3000, "responseLimit": 100}
        if data.mode == "extended":
            # slskd exposes this quiet-period timeout in milliseconds.
            payload.update({"searchTimeout": 30000, "fileLimit": 10000, "responseLimit": 250})
        return await soulseek.request("POST", "/searches", payload)

    @app.get("/api/soulseek/searches/{search_id}")
    async def search_results(search_id: UUID, since: str | None = Query(default=None, max_length=64)):
        status = await soulseek.request("GET", f"/searches/{search_id}")
        counters = ("responseCount", "fileCount", "lockedFileCount")
        values = [status.get(key) if isinstance(status, dict) else None for key in counters]
        valid_counters = all(type(value) is int and value >= 0 for value in values)
        revision = f"v2:{':'.join(str(value) for value in values)}" if valid_counters else None
        terminal_states = {"completed", "cancelled", "timedout", "errored"}
        terminal = isinstance(status, dict) and (
            status.get("isComplete") is True
            or any(
                isinstance(status.get(key), str)
                and any(
                    flag.strip().casefold() in terminal_states
                    for flag in status[key].split(",")
                )
                for key in ("state", "status")
            )
        )
        if valid_counters and since == revision and not terminal:
            # Skip only after an earlier response snapshot established that
            # these counters describe a complete payload.
            return {"search": status, "responses": None, "revision": revision}
        responses = await soulseek.request("GET", f"/searches/{search_id}/responses")
        valid_snapshot = isinstance(responses, list) and all(
            isinstance(response, dict)
            and isinstance(response.get("files"), list)
            and ("lockedFiles" not in response or isinstance(response["lockedFiles"], list))
            for response in responses
        )
        if valid_counters and valid_snapshot:
            actual_counts = [
                len(responses),
                sum(len(response["files"]) for response in responses),
                sum(len(response.get("lockedFiles", [])) for response in responses),
            ]
            valid_snapshot = actual_counts == values
        revision = revision if valid_counters and valid_snapshot else None
        return {"search": status, "responses": responses, "revision": revision}

    @app.put("/api/soulseek/searches/{search_id}")
    async def cancel_search(search_id: UUID):
        return await soulseek.request("PUT", f"/searches/{search_id}")

    @app.delete("/api/soulseek/searches/{search_id}")
    async def delete_search(search_id: UUID):
        return await soulseek.request("DELETE", f"/searches/{search_id}")

    @app.post("/api/soulseek/downloads")
    async def download(data: Download):
        async with download_lock:
            transfers = await soulseek.request("GET", "/transfers/downloads")
            if not isinstance(transfers, list):
                raise HTTPException(502, "Could not verify existing downloads. No files were queued.")
            existing = {file.get('filename'): file for peer in transfers if peer.get('username') == data.username
                        for directory in peer.get('directories', []) for file in directory.get('files', [])}
            pending = {}
            for file in data.files:
                old = existing.get(file.filename)
                flags = {part.strip().casefold() for part in str((old or {}).get('state', '')).split(',')}
                retryable = bool(flags & {'cancelled', 'canceled', 'errored', 'rejected', 'aborted', 'timedout'})
                if old and not retryable:
                    if old.get('size') != file.size:
                        raise HTTPException(409, "An existing download has a different size. Review it in Downloads first.")
                    continue
                if file.filename in pending and pending[file.filename]['size'] != file.size:
                    raise HTTPException(409, "Conflicting sizes for the same remote file.")
                pending[file.filename] = file.model_dump()
            result = None
            if pending:
                result = await soulseek.request("POST", f"/transfers/downloads/{segment(data.username)}", list(pending.values()))
                store.event("download", {"username": data.username, "files": len(pending)})
            return {"ok": True, "result": result, "queued": len(pending), "existing": len(data.files) - len(pending)}

    @app.get("/api/soulseek/downloads")
    async def downloads():
        return await soulseek.request("GET", "/transfers/downloads")

    @app.delete("/api/soulseek/downloads/completed")
    async def clear_downloads():
        # slskd removes terminal transfer records; downloaded files stay on disk.
        await soulseek.request("DELETE", "/transfers/downloads/all/completed")
        return {"ok": True}

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
        return run_job("preview", prepare_preview, data.path, str(data.release_id), data.keep_source, job_source=data.path)

    @app.post('/api/imports/manual-preview')
    def manual_preview(data: ManualImport):
        safe_path(settings.inbox, data.path, allow_root=False)
        safe_path(settings.library, data.destination, exists=False, allow_root=False)
        return run_job('preview', prepare_manual, data.path, data.destination, job_source=data.path)

    @app.post('/api/musicbrainz/match-all')
    def match_all(data: MatchAll):
        with operation_lock:
            paths = album_paths(settings, data.path)
            imports, cached = imported_sources(settings), store.matches()
            jobs, skipped_imported, skipped_matched, active = [], 0, 0, 0
            for path in paths:
                digest, rows = snapshot(settings, path)
                if rows and all(source in imports and imports[source]['size'] == size and imports[source]['mtime_ns'] == mtime
                                for source, size, mtime, _ in rows):
                    skipped_imported += 1
                elif path in cached and cached[path]['signature'] == digest and (cached[path]['result'].get('candidates') or cached[path]['result'].get('selected_release_id') or cached[path]['result'].get('recommended_release_id')):
                    skipped_matched += 1
                elif store.active_match(path):
                    active += 1
                else:
                    jobs.append(run_job('match', prepare_match, path, job_source=path))
            return {'jobs': jobs, 'queued': len(jobs), 'skipped_imported': skipped_imported, 'skipped_matched': skipped_matched, 'active': active}

    @app.post("/api/musicbrainz/match")
    def match_metadata(data: AlbumMatch):
        try:
            digest, _ = snapshot(settings, data.path)
            cached = store.matches().get(data.path)
            if data.artist is None and data.album is None and cached and cached['signature'] == digest:
                return {**cached['result'], 'cached': True}
            digest, result = perform_match(settings, data.path, data.artist, data.album)
            store.save_match(data.path, digest, result)
            return result
        except ValueError as error:
            raise HTTPException(400, str(error)) from None
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(502, "Could not match a MusicBrainz release. Check the tags, connection, artist and album.") from None

    @app.post("/api/imports/{plan_id}/apply")
    def import_album(plan_id: UUID):
        plan = store.get_job(str(plan_id))
        if not plan or plan["status"] != "ready" or plan["kind"] not in {'preview', 'match'} or not plan["result"]:
            raise HTTPException(409, "Create a new preview first")
        result = plan['result'].get('plan') if plan['kind'] == 'match' else plan['result']
        if not result:
            raise HTTPException(409, 'Review the match and choose a release first')
        keep_source_override = result.get('keep_source') is True and result.get('delete_source') is False
        if bool(result.get('delete_source')) != store.preferences()['delete_inbox_after_import'] and not keep_source_override:
            raise HTTPException(409, 'Import cleanup setting changed. Create a new preview before importing.')
        if not store.claim_plan(str(plan_id)):
            raise HTTPException(409, "This preview is already in use")
        return run_job("import", apply_manual if result.get("mode") == "manual" else apply_import, result, plan_id=str(plan_id), job_source=result['source'])

    @app.get("/api/jobs")
    def jobs():
        jobs = store.jobs()
        for job in jobs:
            if job['result']:
                value = job['result']
                job['result'] = {key:value[key] for key in ('album','artist','mode','files','cleanup','preview_error') if key in value}
                if job['kind'] == 'match':
                    job['result'].update({key:value.get('match',{}).get(key,'') for key in ('album','artist')})
        return jobs

    @app.delete("/api/jobs")
    def clear_jobs():
        return {"cleared": store.clear_jobs()}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: UUID):
        if not store.get_job(str(job_id)):
            raise HTTPException(404, "Task not found")
        if not store.cancel_job(str(job_id)):
            raise HTTPException(409, "This task can no longer be cancelled. An import already writing files must finish.")
        return {"ok": True}

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
