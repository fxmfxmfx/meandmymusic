"""Authenticated HTTP entry point for the resumable local music agent."""
from concurrent.futures import ThreadPoolExecutor
import fcntl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit
from uuid import uuid4, UUID

import httpx

from app.music_agent import TaskStore, load_config


MAX_BODY = 8192
MAX_PENDING = 8
TERMINAL = {"done", "superseded"}
RETRYABLE = {"needs_review", "waiting", "new", "planned"}
SAFE_FIELDS = {"id", "request", "phase", "status", "intent", "release", "inbox",
               "preview_id", "import_id", "result", "error", "error_code", "source", "transfer"}


def _model_probe(url):
    """Inspect the local endpoint without waking or managing its service."""
    try:
        response = httpx.get(url.rstrip("/") + "/props", timeout=1,
                             trust_env=False, follow_redirects=False)
        if response.status_code == 503:
            return "loading"
        response.raise_for_status()
        props = response.json()
        sleeping = props.get("is_sleeping")
        if not isinstance(sleeping, bool):
            return "unavailable"
        return "sleeping" if sleeping else "ready"
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        return "unavailable"


def _sanitize(task):
    result = {key: task[key] for key in SAFE_FIELDS if key in task}

    def bounded_text(value):
        return value[:500] if isinstance(value, str) else None

    # Summary payloads are selected explicitly to avoid leaking config or internal journal data.
    for key in ("intent", "release", "source", "result", "transfer"):
        value = result.get(key)
        if isinstance(value, dict):
            allowed = {"artist", "album", "title", "id", "date", "country", "tracks",
                       "username", "directory", "total_bytes", "files", "bytes", "expected",
                       "destinations", "cleanup", "status", "source"}
            if key == "source":
                result[key] = {name: bounded_text(value[name]) for name in ("username", "directory")
                               if name in value and isinstance(value[name], str)}
                result[key].update({name: value[name] for name in ("total_bytes", "tracks")
                                    if name in value and type(value[name]) is int})
                if "tracks" not in result[key] and isinstance(value.get("files"), list):
                    result[key]["tracks"] = len(value["files"])
            elif key == "result":
                result[key] = {name: bounded_text(value[name]) for name in ("artist", "album")
                               if name in value and isinstance(value[name], str)}
                result[key].update({name: value[name] for name in ("files", "bytes")
                                    if name in value and type(value[name]) is int})
                destinations = value.get("destinations")
                if isinstance(destinations, list):
                    result[key]["destinations"] = [bounded_text(item) for item in destinations[:20] if isinstance(item, str)]
                cleanup = value.get("cleanup")
                if isinstance(cleanup, dict):
                    result[key]["cleanup"] = {name: bounded_text(cleanup[name]) for name in ("status", "source")
                                               if name in cleanup and isinstance(cleanup[name], str)}
            elif key == "transfer":
                result[key] = {name: bounded_text(value[name]) if name == "status" else value[name]
                               for name in ("status", "bytes", "total_bytes", "files", "expected")
                               if name in value and ((name == "status" and isinstance(value[name], str)) or
                                                     (name != "status" and type(value[name]) is int))}
            else:
                result[key] = {name: (bounded_text(item) if isinstance(item, str) else item)
                               for name, item in value.items() if name in allowed and
                               isinstance(item, (str, int, bool, type(None)))}
    for key in ("request", "error"):
        if key in result and isinstance(result[key], str):
            result[key] = result[key][:2000]
    return result


class MusicAgentServer:
    def __init__(self, *, token=None, bind=None, port=None, allowed_clients=None,
                 config_path=None, state_dir=None, probe=None, popen=None, workers=2):
        self.token = token if token is not None else os.getenv("MUSIC_AI_WEB_TOKEN", "")
        if len(self.token) < 32:
            raise ValueError("MUSIC_AI_WEB_TOKEN must contain at least 32 characters")
        self.bind = bind or os.getenv("MUSIC_AI_BIND", "127.0.0.1")
        self.port = int(port if port is not None else os.getenv("MUSIC_AI_PORT", "8082"))
        clients = allowed_clients if allowed_clients is not None else os.getenv("MUSIC_AI_ALLOWED_CLIENTS", "127.0.0.1,::1")
        self.allowed_clients = {value.strip() for value in clients.split(",") if value.strip()}
        self.config_path = config_path or os.getenv("MUSIC_AI_CONFIG", "/etc/music-ai/config.json")
        self.state_dir = Path(state_dir or os.getenv("MUSIC_AI_STATE", "/var/lib/music-ai/tasks"))
        self.store = TaskStore(self.state_dir)
        self.probe = probe or _model_probe
        self.popen = popen or subprocess.Popen
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="music-agent")
        self.capacity = workers + MAX_PENDING
        self.lock = threading.RLock()
        self.pending = 0
        self.recovery = []
        self.closing = False
        self.children = set()
        self._recover()
        self.httpd = self._http_server()

    def _recover(self):
        with self.lock:
            for task in self._tasks():
                if task.get("origin") == "web" and task.get("status") in {"queued", "running", "waiting"}:
                    if self.pending < self.capacity:
                        self._submit(task["id"])
                    else:
                        self.recovery.append(task["id"])

    def _tasks(self):
        result = []
        for path in sorted(self.state_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                result.append(self.store.load(path.stem))
            except Exception:
                continue
        return result

    def _http_server(self):
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(10)

            def log_message(self, fmt, *args):
                return

            def _auth(self):
                peer = self.client_address[0]
                supplied = self.headers.get("Authorization", "").encode("utf-8")
                expected = ("Bearer " + owner.token).encode("utf-8")
                if peer not in owner.allowed_clients or not hmac.compare_digest(supplied, expected):
                    self._reply(401, {"error": "Unauthorized"})
                    return False
                return True

            def _reply(self, status, payload):
                body = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if not self._auth(): return
                if urlsplit(self.path).path != "/v1/status":
                    self._reply(404, {"error": "Not found"}); return
                try:
                    state = owner.probe(load_config(owner.config_path)["model_url"])
                    if state not in {"sleeping", "ready", "loading", "unavailable"}: state = "unavailable"
                    tasks = [_sanitize(task) for task in owner._tasks() if task.get("origin") == "web"][:20]
                    self._reply(200, {"model_state": state, "tasks": tasks})
                except Exception:
                    self._reply(500, {"error": "Status unavailable"})

            def do_POST(self):
                if not self._auth(): return
                path = urlsplit(self.path).path
                if self.headers.get("Transfer-Encoding"):
                    self._reply(400, {"error": "Transfer encoding is not supported"}); return
                length = self.headers.get("Content-Length", "")
                is_resume = re.fullmatch(r"/v1/tasks/([0-9a-fA-F-]{36})/resume", path)
                if length == "" and is_resume:
                    payload = {}
                    length = "0"
                elif not length.isdigit():
                    self._reply(400, {"error": "A valid Content-Length is required"}); return
                if int(length) > MAX_BODY:
                    self._reply(413, {"error": "Request body is too large"}); return
                if length != "0":
                    try:
                        payload = json.loads(self.rfile.read(int(length)))
                        if not isinstance(payload, dict): raise ValueError
                    except (ValueError, UnicodeDecodeError):
                        self._reply(400, {"error": "Expected a JSON object"}); return
                if path == "/v1/tasks":
                    request = payload.get("request")
                    if not isinstance(request, str) or not 1 <= len(request.strip()) <= 2000:
                        self._reply(400, {"error": "Enter a music request of 1–2000 characters"}); return
                    request = request.strip()
                    with owner.lock:
                        duplicate = next((task for task in owner._tasks()
                                          if task.get("origin") == "web" and task.get("request") == request
                                          and task.get("status") not in TERMINAL), None)
                        if duplicate:
                            self._reply(202, _sanitize(duplicate)); return
                        if owner.pending >= owner.capacity:
                            self._reply(503, {"error": "Task queue is full"}); return
                        try:
                            config = load_config(owner.config_path)
                            task = {"id": str(uuid4()), "request": request, "phase": "start",
                                    "status": "queued", "created": time.time(),
                                    "manager_url": config["manager_url"], "origin": "web"}
                            owner.store.save(task)
                            owner._submit(task["id"])
                        except Exception:
                            self._reply(500, {"error": "Task could not be queued"}); return
                    self._reply(202, _sanitize(task)); return
                match = re.fullmatch(r"/v1/tasks/([0-9a-fA-F-]{36})/resume", path)
                if match:
                    task_id = match.group(1)
                    try:
                        UUID(task_id)
                        with owner.lock:
                            task = owner.store.load(task_id)
                            if task.get("origin") != "web":
                                self._reply(404, {"error": "Task not found"}); return
                            if task.get("status") in {"queued", "running"}:
                                self._reply(202, _sanitize(task)); return
                            if task.get("status") not in RETRYABLE or task.get("phase") in {"done", "superseded"}:
                                self._reply(409, {"error": "Task cannot be resumed"}); return
                            if owner.pending >= owner.capacity:
                                self._reply(409, {"error": "Task queue is full"}); return
                            task["status"] = "queued"
                            owner.store.save(task)
                            owner._submit(task_id)
                        self._reply(202, _sanitize(task)); return
                    except Exception:
                        self._reply(404, {"error": "Task not found"}); return
                self._reply(404, {"error": "Not found"})
        return ThreadingHTTPServer((self.bind, self.port), Handler)

    def _submit(self, task_id):
        if self.pending >= self.capacity: raise RuntimeError("queue full")
        self.pending += 1
        future = self.executor.submit(self._run, task_id)
        future.add_done_callback(lambda _: self._finished())

    def _finished(self):
        with self.lock:
            self.pending = max(0, self.pending - 1)
            if self.recovery and not self.closing:
                self._submit(self.recovery.pop(0))

    def _run(self, task_id):
        # CLI's flock is the authority against another runner or operator process.
        logfile = None
        try:
            logdir = self.state_dir / "logs"
            logdir.mkdir(mode=0o700, exist_ok=True)
            info = logdir.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise OSError("private log directory required")
            logfd = os.open(logdir / (task_id + ".log"), os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            loginfo = os.fstat(logfd)
            if not stat.S_ISREG(loginfo.st_mode) or loginfo.st_uid != os.geteuid() or loginfo.st_mode & 0o077:
                os.close(logfd)
                raise OSError("private regular log file required")
            logfile = os.fdopen(logfd, "ab", buffering=0)
            env = os.environ.copy()
            env.update({"MUSIC_AI_CONFIG": self.config_path, "MUSIC_AI_STATE": str(self.state_dir)})
            child = self.popen([sys.executable, "-m", "app.music_agent", "resume", task_id,
                                "--wait", "86400"], cwd=str(Path(__file__).resolve().parent.parent),
                               env=env, stdin=subprocess.DEVNULL, stdout=logfile,
                               stderr=subprocess.STDOUT, shell=False, close_fds=True)
            with self.lock: self.children.add(child)
            code = child.wait()
            with self.lock: self.children.discard(child)
            if code:
                try:
                    lockfd = os.open(self.store.task_path(task_id).with_suffix(".lock"),
                                     os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
                    try:
                        acquired = True
                        try:
                            fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:
                            acquired = False  # Another CLI process owns this task.
                        if acquired:
                            task = self.store.load(task_id)
                            if task.get("status") == "running":
                                task.update(status="needs_review", error="The music task stopped unexpectedly.")
                                self.store.save(task)
                    finally:
                        os.close(lockfd)
                except Exception:
                    pass
        except Exception:
            try:
                task = self.store.load(task_id)
                if task.get("status") not in TERMINAL:
                    task.update(status="needs_review", error="The music task could not be started.")
                    self.store.save(task)
            except Exception:
                pass
        finally:
            if logfile is not None:
                logfile.close()

    def serve_forever(self):
        self.httpd.serve_forever()

    def shutdown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        # Leave running CLI children alone: imports may be publishing files.
        with self.lock:
            self.closing = True
            self.recovery.clear()
        self.executor.shutdown(wait=False, cancel_futures=True)


def main():
    server = MusicAgentServer()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
