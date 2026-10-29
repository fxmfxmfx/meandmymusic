import json
import threading
import time
import urllib.error
import urllib.request

import httpx
import pytest

from app.music_agent_server import MusicAgentServer, _model_probe


TOKEN = "test-token-that-is-long-enough-for-http-runner"


@pytest.fixture
def server(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"manager_url": "http://localhost:8080", "model_url": "http://127.0.0.1:8081", "password": "do-not-return"}))
    config.chmod(0o600)
    instance = MusicAgentServer(token=TOKEN, bind="127.0.0.1", port=0,
                                config_path=str(config), state_dir=tmp_path / "state",
                                probe=lambda _url: "sleeping", popen=lambda *a, **kw: (_ for _ in ()).throw(OSError("private details")))
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield instance, f"http://127.0.0.1:{instance.httpd.server_port}"
    instance.shutdown()
    thread.join(timeout=2)


def request(server, path, *, method="GET", body=None, token=TOKEN):
    _, base = server
    headers = {}
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(req, timeout=2)
        return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_auth_checked_before_body_and_status_reports_model_state(server):
    code, body = request(server, "/v1/status", token="wrong")
    assert code == 401 and body == {"error": "Unauthorized"}
    status, body = request(server, "/v1/status")
    assert status == 200 and body == {"model_state": "sleeping", "tasks": []}
    status, body = request(server, "/v1/tasks", method="POST", body=b"x" * 8193, token="wrong")
    assert status == 401


def test_request_validation_duplicate_queue_and_durable_worker_failure(server):
    status, body = request(server, "/v1/tasks", method="POST", body={"request": "  "})
    assert status == 400
    status, queued = request(server, "/v1/tasks", method="POST", body={"request": "Björk — Debut"})
    assert status == 202 and queued["status"] == "queued" and queued["phase"] == "start"
    # Same exact nonterminal intent is idempotent, including while the worker starts.
    status, again = request(server, "/v1/tasks", method="POST", body={"request": "Björk — Debut"})
    assert status == 202 and again["id"] == queued["id"]
    deadline = time.time() + 2
    while time.time() < deadline:
        task = server[0].store.load(queued["id"])
        if task.get("status") == "needs_review": break
        time.sleep(.01)
    assert task["status"] == "needs_review"
    assert task["error"] == "The music task could not be started."


def test_status_redacts_private_task_fields(server):
    owner = server[0]
    task = {"id": "ab9957eb-38af-44d0-b21b-117c6cf3279f", "request": "Artist", "phase": "source",
            "status": "waiting", "origin": "web", "manager_url": "http://secret", "password": "secret",
            "source": {"username": "peer", "directory": "folder", "total_bytes": 3, "files": ["a.flac"], "token": "secret"},
            "raw_log": "secret"}
    owner.store.save(task)
    status, body = request(server, "/v1/status")
    assert status == 200
    assert body["tasks"] == [{"id": task["id"], "request": "Artist", "phase": "source", "status": "waiting",
                              "source": {"username": "peer", "directory": "folder", "total_bytes": 3, "tracks": 1}}]


def test_body_limit_and_json_object_only(server):
    status, body = request(server, "/v1/tasks", method="POST", body=b"x" * 8193)
    assert status == 413
    status, body = request(server, "/v1/tasks", method="POST", body=b"[]")
    assert status == 400


def test_short_token_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="32 characters"):
        MusicAgentServer(token="short", state_dir=tmp_path / "state")


@pytest.mark.parametrize(("status", "props", "expected"), [
    (200, {"is_sleeping": True}, "sleeping"),
    (200, {"is_sleeping": False}, "ready"),
    (200, {}, "unavailable"),
    (503, {}, "loading"),
    (502, {}, "unavailable"),
])
def test_model_probe_reads_props_only(monkeypatch, status, props, expected):
    class Response:
        status_code = status

        def raise_for_status(self):
            if status >= 400:
                request = httpx.Request("GET", "http://model/props")
                response = httpx.Response(status, request=request)
                raise httpx.HTTPStatusError("HTTP failure", request=request, response=response)

        def json(self): return props

    monkeypatch.setattr("app.music_agent_server.httpx.get", lambda *a, **kw: Response())
    assert _model_probe("http://model:8081") == expected


def test_resume_accepts_empty_body_and_queue_full_does_not_change_task(server):
    owner = server[0]
    task = {"id": "6e909c3b-a653-4a7d-9d29-2c5019849a2e", "request": "Artist", "phase": "start",
            "status": "needs_review", "origin": "web", "manager_url": "http://localhost:8080"}
    owner.store.save(task)
    owner.pending = owner.capacity
    status, body = request(server, f"/v1/tasks/{task['id']}/resume", method="POST")
    assert status == 409 and owner.store.load(task["id"])["status"] == "needs_review"
    owner.pending = 0
    status, body = request(server, f"/v1/tasks/{task['id']}/resume", method="POST")
    assert status == 202 and body["status"] == "queued"


def test_post_duplicate_requeues_retryable_task_and_active_duplicate_is_idempotent(server):
    owner = server[0]
    task = {"id": "6e909c3b-a653-4a7d-9d29-2c5019849a2e", "request": "madk1d", "phase": "start",
            "status": "needs_review", "origin": "web", "manager_url": "http://localhost:8080"}
    owner.store.save(task)
    started, release = threading.Event(), threading.Event()
    calls = []

    class Child:
        def wait(self):
            started.set()
            release.wait(2)
            return 0

    def popen(*_args, **_kwargs):
        calls.append(1)
        return Child()

    owner.popen = popen
    status, retried = request(server, "/v1/tasks", method="POST", body={"request": "madk1d"})
    assert status == 202 and retried["id"] == task["id"] and retried["status"] == "queued"
    assert started.wait(1)
    for _ in range(3):
        status, repeated = request(server, "/v1/tasks", method="POST", body={"request": "madk1d"})
        assert status == 202 and repeated["id"] == task["id"]
    assert len(calls) == 1
    release.set()


def test_retryable_duplicate_at_capacity_does_not_change_saved_state(server):
    owner = server[0]
    task = {"id": "6e909c3b-a653-4a7d-9d29-2c5019849a2e", "request": "same request", "phase": "start",
            "status": "needs_review", "origin": "web", "manager_url": "http://localhost:8080"}
    owner.store.save(task)
    owner.pending = owner.capacity
    status, body = request(server, "/v1/tasks", method="POST", body={"request": "same request"})
    assert status == 409
    saved = owner.store.load(task["id"])
    assert saved["status"] == "needs_review"


def test_status_sanitizes_transfer_and_import_result(server):
    owner = server[0]
    task = {"id": "ab9957eb-38af-44d0-b21b-117c6cf3279f", "request": "Artist", "phase": "done",
            "status": "done", "origin": "web", "transfer": {"status": "complete", "bytes": 7,
            "files": 2, "expected": 2, "peer_password": "secret"},
            "result": {"artist": "Artist", "album": "Record", "files": 2,
                       "destinations": ["Library/Record"], "cleanup": {"status": "kept", "source": "Inbox/Record",
                       "password": "secret"}, "raw": "secret"}}
    owner.store.save(task)
    status, body = request(server, "/v1/status")
    assert status == 200
    result = body["tasks"][0]
    assert result["transfer"] == {"status": "complete", "bytes": 7, "files": 2, "expected": 2}
    assert result["result"] == {"artist": "Artist", "album": "Record", "files": 2,
                                "destinations": ["Library/Record"],
                                "cleanup": {"status": "kept", "source": "Inbox/Record"}}
