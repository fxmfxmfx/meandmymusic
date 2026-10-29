"""Resumable artist-wide batches built from the single-album workflow."""
from __future__ import annotations

import fcntl
import hashlib
import os
import time
from contextlib import contextmanager
from uuid import UUID, uuid5, NAMESPACE_URL

from app.music_agent_catalog import artist_albums
from app.music_agent_core import AgentError, _get_search, normalize, transfer_status


def _child_id(parent_id, release):
    key = str(release.get("release_group") or normalize(release.get("title")))
    return str(uuid5(UUID(parent_id), key))


@contextmanager
def _artist_lock(task, store):
    identity = normalize(task["intent"]["artist"]) + "\0" + str(task.get("manager_url", ""))
    name = hashlib.sha256(identity.encode()).hexdigest()
    fd = os.open(store.path / ("artist-" + name + ".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AgentError("A batch for this artist is already running. Resume it after it finishes.") from None
        yield
    finally:
        os.close(fd)


def _snapshot(child):
    result = {"id": child["id"], "album": child["intent"]["album"],
              "phase": child.get("phase", "release"), "status": child.get("status", "new")}
    for key in ("error", "transfer", "result"):
        if key in child:
            value = child[key]
            if key == "transfer" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k in ("bytes", "total_bytes", "files", "expected") and type(v) in (int, float)}
            elif key == "result" and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k in ("files", "artist", "album") and type(v) in (str, int, float)}
            result[key] = value
    return result


def _publish(task, store, children, *, status=None, phase="artist"):
    items = [_snapshot(child) for child in children]
    done = sum(item["status"] == "done" for item in items)
    review = sum(item["status"] == "needs_review" for item in items)
    waiting = sum(item["status"] in ("waiting", "running", "new") for item in items)
    current = next((item["album"] for item in items if item["status"] in ("running", "new", "waiting")), None)
    batch = {"artist": task["intent"]["artist"], "total": len(items), "completed": done,
             "needs_review": review, "waiting": waiting, "current": current, "items": items}
    if task.get("batch") != batch or (status and task.get("status") != status) or task.get("phase") != phase:
        task.update(batch=batch, phase=phase, updated=time.time())
        if status:
            task["status"] = status
        if status == "needs_review":
            task.pop("error", None)
        store.save(task)
    return batch


def run_artist_batch(task, store, api, model, run_album, *, plan=False, wait=3600, poll=10):
    """Discover an artist catalog once, then queue/resume each verified album."""
    if not 0 <= wait <= 86400 or poll <= 0:
        raise AgentError("Batch wait and poll limits are invalid.")
    task.setdefault("kind", "artist")
    task.setdefault("phase", "intent")
    if not task.get("intent") or not task["intent"].get("artist"):
        task["intent"] = model.intent(task["request"])
    if task["intent"].get("album"):
        raise AgentError("Artist batches require an artist-only request.")

    # Keep the artist-wide lock across discovery, child execution and polling.
    with _artist_lock(task, store):
        artist = task["intent"]["artist"]
        task.update(kind='artist', phase='artist', status='running', updated=time.time())
        store.save(task)
        children = task.get("children", [])
        responses = None
        if not children:
            releases = artist_albums(api, artist)
            # One extended artist search is shared in memory by all album workflows.
            responses = _get_search(api, artist, 70, None, mode="extended")
            children = []
            for release in releases:
                child = {"id": _child_id(task["id"], release), "origin": "artist-child",
                         "parent_id": task["id"], "manager_url": task.get("manager_url"),
                         "request": f'{artist} — {release["title"]}',
                         "intent": {"artist": artist, "album": release["title"]},
                         "release": {**release, "accept_recommended_edition": True},
                         "phase": "release", "status": "new", "created": time.time()}
                try:
                    child = store.load(child["id"])
                except AgentError:
                    store.save(child)
                children.append(child)
            task["children"] = [child["id"] for child in children]
            task["phase"] = "artist"
            task["status"] = "running"
            store.save(task)
        else:
            loaded = []
            for child_id in children:
                try:
                    loaded.append(store.load(child_id))
                except AgentError:
                    raise AgentError("An artist batch album task is missing or invalid. Inspect the saved tasks before retrying.") from None
            children = loaded

        deadline = time.monotonic() + wait
        # Initial pass queues every album before waiting on any one peer.
        _publish(task, store, children, status="running")
        for index, child in enumerate(children):
            if child.get("status") == "done" or (plan and child.get("status") == "planned"):
                continue
            if child.get("phase") == "downloading" and child.get("status") in ("downloading", "waiting", "running"):
                continue
            child_lock = store.lock(child["id"])
            try:
                child_lock.__enter__()
            except AgentError as error:
                if "already running" in str(error).casefold():
                    children[index] = store.load(child["id"])
                    continue
                raise
            try:
                child = store.load(child["id"])
                if child.get("status") == "done" or (plan and child.get("status") == "planned"):
                    children[index] = child
                    continue
                run_album(child, store, api, model, plan=plan, wait=0,
                          poll=poll, initial_responses=responses)
                children[index] = store.load(child["id"])
            except AgentError as error:
                child["status"] = "needs_review"
                child["error"] = str(error)
                store.save(child)
                children[index] = store.load(child["id"])
            except OSError:
                child["status"] = "needs_review"
                child["error"] = "Could not read or save this album task state."
                store.save(child)
                children[index] = store.load(child["id"])
            finally:
                child_lock.__exit__(None, None, None)
            _publish(task, store, children, status="running")

        while not plan and time.monotonic() < deadline:
            pending = False
            for index, child in enumerate(children):
                if child.get("phase") != "downloading" or child.get("status") not in ("downloading", "waiting", "running"):
                    continue
                pending = True
                child_lock = store.lock(child["id"])
                try:
                    child_lock.__enter__()
                except AgentError as error:
                    if "already running" in str(error).casefold():
                        children[index] = store.load(child["id"])
                        continue
                    raise
                try:
                    child = store.load(child["id"])
                    if child.get("phase") != "downloading" or child.get("status") not in ("downloading", "waiting", "running"):
                        children[index] = child
                        continue
                    state = transfer_status(api, child["source"])
                    transfer = {k: state.get(k, 0) for k in ("bytes", "total_bytes", "files")}
                    transfer["expected"] = len(child["source"].get("files", []))
                    if child.get("transfer") != transfer:
                        child["transfer"] = transfer
                        child["updated"] = time.time()
                        store.save(child)
                    if state.get("status") == "waiting":
                        children[index] = child
                        continue
                    run_album(child, store, api, model, plan=False, wait=0,
                              poll=poll, initial_responses=responses)
                    children[index] = store.load(child["id"])
                except AgentError as error:
                    child["status"] = "needs_review"
                    child["error"] = str(error)
                    store.save(child)
                    children[index] = store.load(child["id"])
                except OSError:
                    child["status"] = "needs_review"
                    child["error"] = "Could not read or save this album task state."
                    store.save(child)
                    children[index] = store.load(child["id"])
                finally:
                    child_lock.__exit__(None, None, None)
            children = [store.load(child["id"]) for child in children]
            _publish(task, store, children, status="running")
            if not pending or time.monotonic() >= deadline:
                break
            time.sleep(min(poll, max(0, deadline - time.monotonic())))

        children = [store.load(child["id"]) for child in children]
        batch = _publish(task, store, children,
                         status="planned" if plan else ("done" if all(c.get("status") == "done" for c in children)
                         else "needs_review" if not any(c.get("status") in ("downloading", "waiting", "new", "running") for c in children)
                                                         else "waiting"),
                         phase="done" if not plan and all(c.get("status") == "done" for c in children) else "artist")
        if task["status"] == "done":
            result_files = sum((c.get("result") or {}).get("files", 0) for c in children)
            task["result"] = {"artist": artist, "albums": batch["completed"], "files": result_files}
            store.save(task)
        return {"id": task["id"], "phase": task["phase"], "status": task["status"], **batch,
                **({"result": task["result"]} if task.get("result") else {})}
