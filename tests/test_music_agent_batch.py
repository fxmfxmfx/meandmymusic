import uuid
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

import app.music_agent_batch as batch
from app.music_agent import TaskStore
from app.music_agent_core import AgentError


def _release(number, title):
    return {"id": str(uuid.UUID(int=number)), "release_group": str(uuid.UUID(int=100 + number)),
            "artist": "Artist", "title": title, "tracks": 2, "editions": []}


class API:
    pass


class Model:
    pass


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.tasks = {}

    def save(self, task):
        self.tasks[task["id"]] = deepcopy(task)

    def load(self, task_id):
        if task_id not in self.tasks:
            raise AgentError("task missing")
        return deepcopy(self.tasks[task_id])

    @contextmanager
    def lock(self, task_id):
        yield


@pytest.fixture
def setup_batch(tmp_path, monkeypatch):
    store = Store(tmp_path)
    task = {"id": str(uuid.uuid4()), "request": "Artist", "manager_url": "http://manager",
            "kind": "artist", "phase": "intent", "status": "new",
            "intent": {"artist": "Artist", "album": ""}}
    store.save(task)
    releases = [_release(1, "First"), _release(2, "Second")]
    monkeypatch.setattr(batch, "artist_albums", lambda *a, **k: releases)
    monkeypatch.setattr(batch, "_get_search", lambda *a, **k: [{"username": "peer"}])
    return task, store, API(), Model()


def test_batch_persists_deterministic_children_and_resumes_without_requeue(setup_batch):
    task, store, api, model = setup_batch
    calls = []

    def run_album(child, store, api, model, **kwargs):
        calls.append((child["id"], kwargs["initial_responses"]))
        child["phase"] = "downloading"
        child["status"] = "waiting"
        child["source"] = {"username": "peer", "files": [{"filename": "x.flac", "size": 1}]}
        store.save(child)
        return child

    result = batch.run_artist_batch(task, store, api, model, run_album, wait=0)
    children = [store.load(child_id) for child_id in task["children"]]
    assert result["status"] == "waiting"
    assert len(calls) == 2 and all(call[1] == [{"username": "peer"}] for call in calls)
    assert all(child["origin"] == "artist-child" and child["parent_id"] == task["id"] for child in children)

    batch.run_artist_batch(store.load(task["id"]), store, api, model, run_album, wait=0)
    assert len(calls) == 2
    assert task["children"] == [child["id"] for child in children]


def test_failed_album_is_reviewable_and_other_album_completes(setup_batch, monkeypatch):
    task, store, api, model = setup_batch
    states = iter(["complete"])

    def transfer(_api, _source):
        return {"status": next(states), "bytes": 2, "total_bytes": 2, "files": 1}

    monkeypatch.setattr(batch, "transfer_status", transfer)

    def run_album(child, store, api, model, **kwargs):
        if child["intent"]["album"] == "First":
            raise AgentError("No complete FLAC folder matched.")
        child.update(phase="done", status="done", result={"files": 2})
        store.save(child)
        return child

    # The first invocation records one review failure; establish the other as queued.
    def queue_then_fail(child, store, api, model, **kwargs):
        if child["intent"]["album"] == "First":
            raise AgentError("No complete FLAC folder matched.")
        child.update(phase="downloading", status="waiting", source={"username": "peer", "files": [{"filename": "x.flac", "size": 2}]})
        store.save(child)
    first = batch.run_artist_batch(task, store, api, model, queue_then_fail, wait=0)
    assert first["needs_review"] == 1
    resumed = batch.run_artist_batch(store.load(task["id"]), store, api, model, run_album, wait=1, poll=0.01)
    assert resumed["completed"] == 1
    assert resumed["needs_review"] == 1
    assert resumed["status"] == "needs_review"


def test_plan_never_runs_transfer_or_mutating_callback(setup_batch, monkeypatch):
    task, store, api, model = setup_batch
    called = []

    def plan_album(child, store, api, model, **kwargs):
        assert kwargs["plan"] is True and kwargs["wait"] == 0
        child.update(phase="source", status="planned")
        store.save(child)
        called.append(child["id"])

    monkeypatch.setattr(batch, "transfer_status", lambda *a: pytest.fail("plan polled transfers"))
    result = batch.run_artist_batch(task, store, api, model, plan_album, plan=True, wait=0)
    assert result["status"] == "planned"
    assert len(called) == 2


def test_artist_lock_rejects_parallel_batch(setup_batch):
    task, store, api, model = setup_batch
    with batch._artist_lock(task, store):
        with pytest.raises(AgentError, match="already running"):
            batch.run_artist_batch(task, store, api, model, lambda *a, **k: None, wait=0)


def _real_store_batch(tmp_path, monkeypatch):
    store = TaskStore(tmp_path)
    task = {"id": str(uuid.uuid4()), "request": "Artist", "manager_url": "http://manager",
            "kind": "artist", "phase": "intent", "status": "new",
            "intent": {"artist": "Artist", "album": ""}}
    store.save(task)
    monkeypatch.setattr(batch, "artist_albums", lambda *a, **k: [_release(1, "First")])
    monkeypatch.setattr(batch, "_get_search", lambda *a, **k: [{"username": "peer"}])
    return task, store, API(), Model()


def test_real_task_store_updates_child_after_initial_queue_and_polls_to_done(tmp_path, monkeypatch):
    task, store, api, model = _real_store_batch(tmp_path, monkeypatch)
    calls = []

    def run_album(child, store, api, model, **kwargs):
        calls.append(child["phase"])
        if not child.get("source"):
            child.update(phase="downloading", status="waiting",
                         source={"username": "peer", "files": [{"filename": "x.flac", "size": 2}]})
        else:
            child.update(phase="done", status="done", result={"files": 1})
        store.save(child)

    monkeypatch.setattr(batch, "transfer_status", lambda *a: {
        "status": "complete", "bytes": 2, "total_bytes": 2, "files": 1})
    result = batch.run_artist_batch(task, store, api, model, run_album, wait=0.5, poll=0.01)
    child = store.load(task["children"][0])
    assert result["status"] == "done"
    assert calls == ["release", "downloading"]
    assert child["status"] == "done"
    assert child["release"]["accept_recommended_edition"] is True


def test_planned_child_is_processed_when_resuming_as_normal_run(setup_batch):
    task, store, api, model = setup_batch
    def make_plan(child, store, *a, **k):
        child.update(phase="source", status="planned")
        store.save(child)
    first = batch.run_artist_batch(task, store, api, model, make_plan, plan=True, wait=0)
    assert first["status"] == "planned"
    calls = []

    def resume(child, store, api, model, **kwargs):
        calls.append(child["id"])
        child.update(phase="done", status="done", result={"files": 1})
        store.save(child)

    result = batch.run_artist_batch(store.load(task["id"]), store, api, model, resume, wait=0)
    assert result["status"] == "done"
    assert len(calls) == 2


def test_locked_child_is_skipped_without_changing_its_state(tmp_path, monkeypatch):
    task, store, api, model = _real_store_batch(tmp_path, monkeypatch)
    def queue(child, store, *a, **k):
        child.update(phase="downloading", status="waiting",
                     source={"username": "peer", "files": [{"filename": "x.flac", "size": 1}]})
        store.save(child)
    batch.run_artist_batch(task, store, api, model, queue, wait=0)
    child_id = task["children"][0]
    before = store.load(child_id)
    calls = []
    with store.lock(child_id):
        result = batch.run_artist_batch(store.load(task["id"]), store, api, model,
                                        lambda *a, **k: calls.append("called"), wait=0.05, poll=0.01)
    after = store.load(child_id)
    assert not calls
    assert after["status"] == before["status"] == "waiting"
    assert "error" not in after
    assert result["status"] == "waiting"

