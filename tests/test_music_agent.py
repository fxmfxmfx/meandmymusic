import json
from pathlib import Path
import stat

import httpx
import pytest

from app import music_agent as agent
from app.music_agent_core import AgentError


def test_local_model_receives_only_request_and_fixed_schema():
    sent = []
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": '{"artist":"Arvo Pärt","album":"Alina","needs_input":false}'}}]})
    model = agent.LocalModel("http://localhost:8081", transport=httpx.MockTransport(handle))
    try:
        assert model.intent("скачай Arvo Pärt — Alina") == {"artist": "Arvo Pärt", "album": "Alina"}
        assert sent[0]["response_format"]["json_schema"]["strict"] is True
        assert sent[0]["max_tokens"] == 256
        assert sent[0]["reasoning_budget_tokens"] == 0
        assert "password" not in sent[0]["messages"][1]["content"]
    finally:
        model.close()


@pytest.mark.parametrize("decision", [{"index": 99}, {"index": True}, {"index": -1}])
def test_model_cannot_invent_candidate_indices(decision):
    model = agent.LocalModel("http://localhost")
    model.decision = lambda *args: decision
    try:
        with pytest.raises(AgentError):
            model.choose("source", "album", [{"user": "real"}])
    finally:
        model.close()


def test_truncated_model_output_is_not_an_executable_decision():
    def handle(request):
        return httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {
            "content": '{"artist":"Artist","album":"Album","needs_input":false}'}}]})
    model = agent.LocalModel("http://localhost", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(AgentError):
            model.intent("Artist Album")
    finally:
        model.close()


def test_configuration_must_be_private_and_never_reports_secrets(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"manager_url": "http://manager", "password": "DO-NOT-PRINT"}))
    path.chmod(0o644)
    with pytest.raises(AgentError) as error:
        agent.load_config(path)
    assert "DO-NOT-PRINT" not in str(error.value)
    path.chmod(0o600)
    assert agent.load_config(path)["password"] == "DO-NOT-PRINT"
    symlink = tmp_path / "linked.json"
    symlink.symlink_to(path)
    with pytest.raises(AgentError):
        agent.load_config(symlink)


def test_task_journal_is_private_atomic_and_locked(tmp_path):
    store = agent.TaskStore(tmp_path / "tasks")
    task = {"id": str(agent.uuid4()), "phase": "queuing"}
    store.save(task)
    assert store.load(task["id"]) == task
    assert store.task_path(task["id"]).stat().st_mode & 0o077 == 0
    with store.lock(task["id"]):
        with pytest.raises(AgentError):
            with store.lock(task["id"]):
                pass
    with pytest.raises(AgentError):
        store.task_path("../../etc/shadow")


def test_task_checkpoint_durably_publishes_rename_before_returning(tmp_path, monkeypatch):
    store = agent.TaskStore(tmp_path / "tasks")
    calls = []
    sync = agent.os.fsync
    def record(fd):
        calls.append(agent.os.fstat(fd).st_mode)
        sync(fd)
    monkeypatch.setattr(agent.os, "fsync", record)
    store.save({"id": str(agent.uuid4()), "phase": "queuing"})
    assert len(calls) == 2 and stat.S_ISREG(calls[0]) and stat.S_ISDIR(calls[1])


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    calls = []
    source = {"username": "peer", "directory": "Artist/Album", "files": [
        {"filename": "Artist\\Album\\1.flac", "size": 10}], "total_bytes": 10}
    release = {"id": str(agent.uuid4()), "artist": "Artist", "title": "Album", "tracks": 1}
    task = {"id": str(agent.uuid4()), "request": "Artist Album", "phase": "start"}
    store = agent.TaskStore(tmp_path / "tasks")
    class API:
        def call(self, method, path, data=None, params=None):
            calls.append((method, path))
            if path == "/preferences":
                return {"delete_inbox_after_import": False}
            if path.endswith("/apply"):
                return {"id": "import-id"}
            return {"id": "preview-id", "status": "ready"}
    class Model:
        def intent(self, request):
            return {"artist": "Artist", "album": "Album"}
        def choose(self, *args):
            return 0
    monkeypatch.setattr(agent, "release_candidates", lambda *a, **k: [release])
    monkeypatch.setattr(agent, "search_sources", lambda *a, **k: [source])
    monkeypatch.setattr(agent, "queue_source", lambda *a: calls.append("queue"))
    monkeypatch.setattr(agent, "transfer_status", lambda *a: {"status": "complete", "files": 1, "found": 1, "bytes": 10})
    monkeypatch.setattr(agent, "discover_inbox", lambda *a: "Album")
    monkeypatch.setattr(agent, "get_match", lambda *a: calls.append("match"))
    monkeypatch.setattr(agent, "create_preview", lambda *a: {"id": "preview-id"})
    monkeypatch.setattr(agent, "inspect_preview", lambda *a: calls.append("review"))
    monkeypatch.setattr(agent, "wait_job", lambda api, jid: {
        "id": jid, "status": "done" if jid == "import-id" else "ready", "result": {"files": 1}})
    return task, store, API(), Model(), source, calls


def test_workflow_matches_reviews_applies_and_checks_final_job(workflow):
    task, store, api, model, _, calls = workflow
    result = agent.run_workflow(task, store, api, model)
    assert result["phase"] == "done" and result["result"]["files"] == 1
    assert calls.index("queue") < calls.index("match") < calls.index("review")
    assert calls.count("review") == 2
    assert ("POST", "/imports/preview-id/apply") in calls


def test_plan_never_queues_or_applies(workflow):
    task, store, api, model, _, calls = workflow
    result = agent.run_workflow(task, store, api, model, plan=True)
    assert result["status"] == "planned" and task["phase"] == "source"
    assert not calls


def test_empty_saved_sources_are_researched_on_resume(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="sources", sources=[], release={"artist": "Artist", "title": "Album"})
    searches = []
    def search(*args, **kwargs):
        searches.append(True)
        return [source]
    monkeypatch.setattr(agent, "search_sources", search)
    result = agent.run_workflow(task, store, api, model, plan=True)
    assert searches == [True] and result["status"] == "planned"
    assert task["source"] == source and not calls


def test_nonempty_saved_sources_do_not_repeat_network_search(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="sources", sources=[source])
    def forbidden(*args, **kwargs):
        raise AssertionError("Previously found candidates must not be discarded")
    monkeypatch.setattr(agent, "search_sources", forbidden)
    assert agent.run_workflow(task, store, api, model, plan=True)["status"] == "planned"
    assert not calls


def test_uncertain_queue_is_not_repeated(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="queuing", source=source)
    monkeypatch.setattr(agent, "transfer_status", lambda *a: {"status": "waiting", "found": 0})
    with pytest.raises(AgentError, match="unknown outcome"):
        agent.run_workflow(task, store, api, model)
    assert "queue" not in calls


def test_failed_transfer_never_reaches_match(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="downloading", source=source)
    monkeypatch.setattr(agent, "transfer_status", lambda *a: {"status": "failed", "files": 0, "bytes": 0})
    with pytest.raises(AgentError):
        agent.run_workflow(task, store, api, model)
    assert "match" not in calls and "review" not in calls


def test_waiting_task_can_be_resumed_without_requeue(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="downloading", source=source)
    monkeypatch.setattr(agent, "transfer_status", lambda *a: {"status": "waiting", "files": 0, "bytes": 0})
    result = agent.run_workflow(task, store, api, model, wait=0)
    assert result["status"] == "waiting" and store.load(task["id"])["phase"] == "downloading"
    assert not calls


def test_uncertain_import_is_not_repeated(workflow):
    task, store, api, model, _, calls = workflow
    task.update(phase="applying", preview_id="preview-id")
    with pytest.raises(AgentError, match="unknown outcome"):
        agent.run_workflow(task, store, api, model)
    assert not calls


def test_failed_preview_review_prevents_import(workflow, monkeypatch):
    task, store, api, model, source, calls = workflow
    task.update(phase="reviewed", preview_id="preview-id", source=source, inbox="Album", release={})
    def reject(*args):
        raise AgentError("Wrong edition")
    monkeypatch.setattr(agent, "inspect_preview", reject)
    with pytest.raises(AgentError, match="Wrong edition"):
        agent.run_workflow(task, store, api, model)
    assert not any(method == "POST" for method, path in calls)


def test_full_api_workflow_uses_real_validation_and_import_contract(tmp_path):
    release = {"id": str(agent.uuid4()), "artist": "Artist", "title": "Album", "tracks": 1}
    remote = {"filename": "Artist\\Album\\01.flac", "size": 10}
    trace = []
    preview = {"id": "preview-id", "status": "ready", "result": {
        "mode": "copy", "delete_source": False, "source": "Album", "created": agent.time.time(),
        "release_id": release["id"], "artist": "Artist", "album": "Album", "confidence": 99,
        "rows": [{"source": "Album/01.flac", "destination": "Artist/Album/1-1 Track.flac",
                  "fingerprint": {"size": 10},
                  "after": {"albumartist": "Artist", "album": "Album", "mb_albumid": release["id"]}}]}}
    def handler(request):
        path = request.url.path
        trace.append((request.method, path))
        assert request.headers["X-Requested-With"] == "meandmymusic"
        if path == "/api/login":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/musicbrainz/releases":
            return httpx.Response(200, json=[release])
        if path == "/api/soulseek/searches":
            return httpx.Response(200, json={"id": "search-id"})
        if path == "/api/soulseek/searches/search-id":
            return httpx.Response(200, json={"search": {
                "state": "Completed, TimedOut", "fileCount": 1, "responseCount": 1, "lockedFileCount": 0},
                "responses": [{"username": "peer", "hasFreeUploadSlot": True, "files": [remote]}]})
        if path == "/api/preferences":
            return httpx.Response(200, json={"delete_inbox_after_import": False})
        if path == "/api/soulseek/downloads":
            if request.method == "POST":
                assert json.loads(request.content) == {"username": "peer", "files": [remote]}
                return httpx.Response(200, json={"queued": 1})
            return httpx.Response(200, json=[{"username": "peer", "directories": [{"files": [
                {**remote, "state": "Completed, Succeeded", "bytesTransferred": 10}]}]}])
        if path == "/api/files":
            entries = ([{"path": "Album", "directory": True}] if request.url.params["path"] == ""
                       else [{"path": "Album/01.flac", "name": "01.flac", "size": 10, "audio": True}])
            return httpx.Response(200, json={"entries": entries})
        if path == "/api/musicbrainz/match":
            return httpx.Response(200, json={"recommended_release_id": release["id"], "candidates": [
                {**release, "confidence": 99, "missing_tracks": 0, "extra_files": 0}]})
        if path == "/api/imports/preview":
            assert json.loads(request.content)["keep_source"] is True
            return httpx.Response(200, json={"id": "preview-id"})
        if path == "/api/jobs/preview-id":
            return httpx.Response(200, json=preview)
        if path == "/api/imports/preview-id/apply":
            return httpx.Response(200, json={"id": "import-id"})
        if path == "/api/jobs/import-id":
            return httpx.Response(200, json={"status": "done", "result": {"files": 1}})
        raise AssertionError(f"Unexpected API call: {request.method} {path}")
    class Model:
        def intent(self, request):
            return {"artist": "Artist", "album": "Album"}
        def choose(self, *args):
            return 0
    api = agent.MusicAPI("http://manager", "secret", transport=httpx.MockTransport(handler))
    try:
        task = {"id": str(agent.uuid4()), "request": "Artist Album", "phase": "start"}
        result = agent.run_workflow(task, agent.TaskStore(tmp_path / "tasks"), api, Model())
        assert result["status"] == "done" and result["result"]["files"] == 1
        assert trace.count(("POST", "/api/soulseek/downloads")) == 1
        assert trace.index(("POST", "/api/musicbrainz/match")) < trace.index(("POST", "/api/imports/preview-id/apply"))
    finally:
        api.close()
