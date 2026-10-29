"""Small authenticated HTTP adapter for the optional music assistant."""
from __future__ import annotations

from urllib.parse import urlsplit
from uuid import UUID

import httpx
from fastapi import HTTPException
from app.music_agent_schema import public_batch


UNAVAILABLE = "Music assistant is unavailable. Check the agent service."
_MODEL_STATES = {"sleeping", "ready", "loading", "unavailable"}
_PHASES = {"start", "intent", "release", "sources", "source", "queuing", "downloading",
           "downloaded", "inbox", "matched", "preview", "reviewed", "applying", "importing", "done", "artist"}
_STATUSES = {"queued", "running", "waiting", "planned", "needs_review", "done", "superseded", "failed"}


def _url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError
        return value.rstrip("/")
    except (ValueError, TypeError):
        raise HTTPException(503, UNAVAILABLE) from None


def _string(value, limit=500):
    return value[:limit] if isinstance(value, str) else None


def _integer(value):
    return value if type(value) is int and value >= 0 else None


def _string_list(value, *, limit=100, item_limit=1000):
    if not isinstance(value, list):
        return None
    return [item[:item_limit] for item in value[:limit] if isinstance(item, str)]


def _cleanup(value):
    if not isinstance(value, dict):
        return None
    result = {}
    for key, limit in {"status": 40, "source": 1000, "message": 500}.items():
        item = _string(value.get(key), limit)
        if item is not None:
            result[key] = item
    return result


def _object(value, fields):
    if not isinstance(value, dict):
        return None
    result = {}
    for key, limit in fields.items():
        item = value.get(key)
        if key in {"tracks", "files", "bytes", "total_bytes", "expected", "albums"}:
            parsed = _integer(item)
        elif key == "destinations":
            parsed = _string_list(item)
        elif key == "cleanup":
            parsed = _cleanup(item)
        elif key == "id":
            try:
                parsed = str(UUID(item)) if isinstance(item, str) else None
            except ValueError:
                parsed = None
        else:
            parsed = _string(item, limit)
        if parsed is not None:
            result[key] = parsed
    return result


def _task(value):
    if not isinstance(value, dict):
        return None
    try:
        task_id = str(UUID(value.get("id")))
    except (ValueError, TypeError, AttributeError):
        return None
    phase, status = value.get("phase"), value.get("status")
    if phase not in _PHASES or status not in _STATUSES:
        return None
    task = {"id": task_id, "phase": phase, "status": status}
    if isinstance(value.get('batch'), dict):
        task['batch'] = public_batch(value['batch'])
    if isinstance(value.get("request"), str):
        task["request"] = value["request"][:2000]
    for key, fields in {
        "intent": {"artist": 300, "album": 300},
        "release": {"artist": 300, "title": 300, "date": 40, "tracks": 0, "id": 0},
        "source": {"username": 200, "directory": 1000, "tracks": 0, "total_bytes": 0},
        "result": {"artist": 300, "album": 300, "files": 0, "albums": 0, "destinations": 0, "cleanup": 0},
        "transfer": {"bytes": 0, "total_bytes": 0, "files": 0, "expected": 0},
    }.items():
        obj = _object(value.get(key), fields)
        if obj:
            task[key] = obj
    for key, limit in {"inbox": 1000, "error": 1000, "error_code": 100, "preview_id": 100, "import_id": 100}.items():
        val = _string(value.get(key), limit)
        if val is not None:
            task[key] = val
    return task


class MusicAgentProxy:
    def __init__(self, settings, *, transport=None):
        self.base_url = getattr(settings, "music_ai_url", "")
        self.token = getattr(settings, "music_ai_token", "")
        self.transport = transport

    @property
    def configured(self):
        return bool(self.base_url and self.token)

    async def _request(self, method, path, *, expected_status=200, **kwargs):
        if not self.configured:
            raise HTTPException(503, "Music assistant is not configured.")
        base = _url(self.base_url)
        try:
            async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                         follow_redirects=False, timeout=5.0) as client:
                response = await client.request(method, base + path,
                    headers={"Authorization": "Bearer " + self.token}, **kwargs)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, UNAVAILABLE) from None
        if response.status_code in {401, 403}:
            raise HTTPException(503, "Music assistant authentication failed. Check its configuration.")
        if response.status_code == 409:
            raise HTTPException(409, "The music assistant is busy. Try again shortly.")
        if response.status_code in {400, 422}:
            raise HTTPException(400, "The music assistant rejected the request.")
        if not response.is_success:
            raise HTTPException(502, "The music assistant returned an error.")
        if response.status_code != expected_status:
            raise HTTPException(502, "The music assistant returned an invalid response.")
        try:
            payload = response.json()
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(502, "The music assistant returned an invalid response.") from None
        if not isinstance(payload, dict):
            raise HTTPException(502, "The music assistant returned an invalid response.")
        return payload

    async def status(self):
        if not self.configured:
            return {"configured": False, "available": False, "model_state": "unavailable", "tasks": []}
        payload = await self._request("GET", "/v1/status")
        state, tasks = payload.get("model_state"), payload.get("tasks")
        if (state not in _MODEL_STATES or not isinstance(tasks, list) or
                any(not isinstance(item, dict) for item in tasks)):
            raise HTTPException(502, "The music assistant returned an invalid response.")
        sanitized = [_task(item) for item in tasks[:20]]
        if any(task is None for task in sanitized):
            raise HTTPException(502, "The music assistant returned an invalid response.")
        return {"configured": True, "available": True, "model_state": state,
                "tasks": sanitized}

    async def submit(self, request: str):
        if not self.configured:
            raise HTTPException(503, "Music assistant is not configured.")
        payload = await self._request("POST", "/v1/tasks", expected_status=202,
                                      json={"request": request})
        task = _task(payload)
        if task is None:
            raise HTTPException(502, "The music assistant returned an invalid response.")
        return task

    async def resume(self, task_id: str):
        if not self.configured:
            raise HTTPException(503, "Music assistant is not configured.")
        try:
            task_id = str(UUID(task_id))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid task ID.") from None
        payload = await self._request("POST", f"/v1/tasks/{task_id}/resume", expected_status=202, json={})
        task = _task(payload)
        if task is None:
            raise HTTPException(502, "The music assistant returned an invalid response.")
        return task
