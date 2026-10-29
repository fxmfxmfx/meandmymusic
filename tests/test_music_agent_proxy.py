from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.music_agent_proxy import MusicAgentProxy


def proxy(handler, *, url="http://192.168.1.20:8082", token="secret-token"):
    return MusicAgentProxy(SimpleNamespace(music_ai_url=url, music_ai_token=token),
                           transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_status_auth_sanitizes_remote_data_and_bypasses_environment(monkeypatch):
    task_id = str(uuid4())
    def handler(request):
        assert request.url.path == "/v1/status"
        assert request.headers["Authorization"] == "Bearer secret-token"
        assert "proxy.local" not in str(request.url)
        return httpx.Response(200, json={"model_state": "ready", "tasks": [{
            "id": task_id, "request": "find album", "phase": "release", "status": "waiting",
            "intent": {"artist": "Artist", "album": "Album", "password": "hidden"},
            "arbitrary": "secret", "source": {"username": "peer", "directory": "dir", "tracks": 3},
        }]})
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.local:8080")
    result = await proxy(handler).status()
    assert result == {"configured": True, "available": True, "model_state": "ready", "tasks": [{
        "id": task_id, "request": "find album", "phase": "release", "status": "waiting",
        "intent": {"artist": "Artist", "album": "Album"},
        "source": {"username": "peer", "directory": "dir", "tracks": 3},
    }]}


@pytest.mark.asyncio
async def test_submit_and_resume_use_fixed_paths_and_uuid():
    task_id = str(uuid4())
    seen = []
    def handler(request):
        seen.append((request.method, request.url.path, request.read()))
        return httpx.Response(202, json={"id": task_id, "request": "request", "phase": "start", "status": "queued",
                                         "error": "safe", "token": "must not leak"})
    client = proxy(handler)
    assert (await client.submit("request"))["id"] == task_id
    assert (await client.resume(task_id))["id"] == task_id
    assert seen == [("POST", "/v1/tasks", b'{"request":"request"}'),
                    ("POST", f"/v1/tasks/{task_id}/resume", b"{}")]
    assert "token" not in await client.submit("request")
    with pytest.raises(HTTPException) as error:
        await client.resume("../../etc/passwd")
    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_disabled_and_malformed_responses_are_safe():
    disabled = MusicAgentProxy(SimpleNamespace(music_ai_url="", music_ai_token=""))
    assert await disabled.status() == {"configured": False, "available": False,
                                       "model_state": "unavailable", "tasks": []}
    with pytest.raises(HTTPException, match="not configured"):
        await disabled.submit("hi")
    client = proxy(lambda request: httpx.Response(200, json={"model_state": "ready", "tasks": [{"oops": 1}]}))
    with pytest.raises(HTTPException) as error:
        await client.status()
    assert error.value.status_code == 502


@pytest.mark.asyncio
async def test_upstream_error_never_exposes_body_or_token():
    client = proxy(lambda request: httpx.Response(401, text="secret-token raw upstream log"))
    with pytest.raises(HTTPException) as error:
        await client.status()
    assert error.value.status_code == 503
    assert "secret-token" not in str(error.value.detail)


@pytest.mark.asyncio
async def test_real_runner_phases_and_import_result_are_sanitized():
    task_id = str(uuid4())
    payload = {"id": task_id, "request": "request", "phase": "importing", "status": "running",
               "transfer": {"bytes": 8, "total_bytes": 12, "files": 2, "expected": 3, "token": "x"},
               "result": {"artist": "Artist", "album": "Album", "files": 2,
                          "destinations": ["Artist/Album/01.flac", "Artist/Album/02.flac"],
                          "cleanup": {"status": "deleted", "source": "folder", "message": "done",
                                      "password": "never expose"}, "token": "never expose"}}
    client = proxy(lambda request: httpx.Response(200, json={"model_state": "ready", "tasks": [payload]}))
    result = await client.status()
    task = result["tasks"][0]
    assert task["phase"] == "importing"
    assert task["transfer"] == {"bytes": 8, "total_bytes": 12, "files": 2, "expected": 3}
    assert task["result"] == {"artist": "Artist", "album": "Album", "files": 2,
                              "destinations": ["Artist/Album/01.flac", "Artist/Album/02.flac"],
                              "cleanup": {"status": "deleted", "source": "folder", "message": "done"}}
    assert "password" not in str(task)


@pytest.mark.asyncio
async def test_downloading_phase_and_planned_status_are_allowed():
    task_id = str(uuid4())
    task = {"id": task_id, "request": "request", "phase": "downloading", "status": "planned"}
    client = proxy(lambda request: httpx.Response(200, json={"model_state": "loading", "tasks": [task]}))
    assert (await client.status())["tasks"][0]["status"] == "planned"


@pytest.mark.asyncio
async def test_artist_batch_progress_is_public_but_private_fields_are_removed():
    child_id = str(uuid4())
    task = {'id': str(uuid4()), 'request': 'madk1d', 'phase': 'artist', 'status': 'running',
            'batch': {'artist': 'madk1d', 'total': 2, 'completed': 1, 'token': 'secret',
                      'items': [{'id': child_id, 'album': 'sexyswag', 'phase': 'downloading',
                                 'status': 'waiting', 'password': 'secret',
                                 'transfer': {'bytes': 10, 'total_bytes': 100, 'files': 0, 'expected': 9, 'token': 'secret'}}]}}
    client = proxy(lambda request: httpx.Response(200, json={'model_state': 'sleeping', 'tasks': [task]}))
    result = (await client.status())['tasks'][0]
    assert result['batch']['completed'] == 1
    assert result['batch']['items'][0]['transfer']['expected'] == 9
    assert 'secret' not in str(result)
