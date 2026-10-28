from types import SimpleNamespace

from app.cli import execute


def test_soulseek_adapter_contract(auth, monkeypatch):
    from app.integrations import Soulseek
    calls = []
    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        if method == 'GET' and path == '/transfers/downloads':
            return []
        return {"id": "f3782ee1-5cd2-45d8-936e-57fd18216b21"}
    monkeypatch.setattr(Soulseek, "request", request)
    assert auth.post("/api/soulseek/searches", json={"query": "Test Artist"}).status_code == 200
    assert calls[-1][2]["searchText"] == "Test Artist"
    assert auth.post("/api/soulseek/downloads", json={"username": "user name", "files": [{"filename": "Music\\song.flac", "size": 123}]}).status_code == 200
    assert calls[-1] == ("POST", "/transfers/downloads/user%20name", [{"filename": "Music\\song.flac", "size": 123}])
    assert auth.post("/api/soulseek/downloads", json={"username": "user", "files": []}).status_code == 422


def test_download_request_preserves_active_and_successful_transfers(auth, monkeypatch):
    from app.integrations import Soulseek
    records = [{"filename": "Album/1.flac", "size": 10, "state": "Queued, Remotely"},
               {"filename": "Album/2.flac", "size": 20, "state": "Completed, Succeeded"}]
    posted = []
    async def request(self, method, path, data=None):
        if method == 'GET':
            return [{"username": "Peer", "directories": [{"files": records}]}]
        posted.append(data)
        records.extend({**f, 'state': 'Queued, Locally'} for f in data)
        return {}
    monkeypatch.setattr(Soulseek, 'request', request)
    body = {'username': 'Peer', 'files': [{'filename': f'Album/{i}.flac', 'size': i * 10} for i in range(1, 4)]}
    first = auth.post('/api/soulseek/downloads', json=body)
    second = auth.post('/api/soulseek/downloads', json=body)
    assert first.json()['queued'] == 1 and second.json()['queued'] == 0
    assert posted == [[{'filename': 'Album/3.flac', 'size': 30}]]
    assert records[1]['state'] == 'Completed, Succeeded'


def test_failed_download_can_be_retried_but_changed_active_size_is_rejected(auth, monkeypatch):
    from app.integrations import Soulseek
    posted = []
    async def request(self, method, path, data=None):
        if method == 'GET':
            return [{'username': 'Peer', 'directories': [{'files': [
                {'filename': 'Album/1.flac', 'size': 10, 'state': 'Completed, Cancelled'},
                {'filename': 'Album/2.flac', 'size': 20, 'state': 'Queued, Remotely'}]}]}]
        posted.append(data)
        return {}
    monkeypatch.setattr(Soulseek, 'request', request)
    assert auth.post('/api/soulseek/downloads', json={'username': 'Peer', 'files': [{'filename': 'Album/1.flac', 'size': 10}]}).json()['queued'] == 1
    assert auth.post('/api/soulseek/downloads', json={'username': 'Peer', 'files': [{'filename': 'Album/2.flac', 'size': 21}]}).status_code == 409
    assert len(posted) == 1


def test_search_trims_and_rejects_short_queries_before_request(auth, monkeypatch):
    from app.integrations import Soulseek
    calls = []
    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {"id": "search-id"}
    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.post("/api/soulseek/searches", json={"query": "  Artist name  "})
    assert response.status_code == 200
    assert calls == [("POST", "/searches", {"searchText": "Artist name", "fileLimit": 3000, "responseLimit": 100})]
    calls.clear()
    response = auth.post("/api/soulseek/searches", json={"query": "   "})
    assert response.status_code == 400
    assert response.json()["detail"] == "Search query must contain at least 2 characters"
    assert calls == []


def test_search_revision_skips_unchanged_responses(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    calls = []
    responses = [
        {"username": "peer-a", "files": [{}, {}, {}], "lockedFiles": [{}]},
        {"username": "peer-b", "files": [{}, {}, {}, {}]},
    ]
    async def request(self, method, path, data=None):
        calls.append((method, path))
        return {"responseCount": 2, "fileCount": 7, "lockedFileCount": 1} if path == f"/searches/{search_id}" else responses
    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.get(f"/api/soulseek/searches/{search_id}?since=v2:2:7:1")
    assert response.status_code == 200
    assert response.json() == {
        "search": {"responseCount": 2, "fileCount": 7, "lockedFileCount": 1},
        "responses": None,
        "revision": "v2:2:7:1",
    }
    assert calls == [("GET", f"/searches/{search_id}")]


def test_search_revision_fetches_full_responses_when_changed_or_initial(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    calls = []
    status = {"responseCount": 0, "fileCount": 0, "lockedFileCount": 0}
    responses = []
    async def request(self, method, path, data=None):
        calls.append((method, path))
        return status if path == f"/searches/{search_id}" else responses
    monkeypatch.setattr(Soulseek, "request", request)

    first = auth.get(f"/api/soulseek/searches/{search_id}")
    assert first.status_code == 200
    assert first.json()["responses"] == responses
    assert first.json()["revision"] == "v2:0:0:0"
    assert calls == [("GET", f"/searches/{search_id}"), ("GET", f"/searches/{search_id}/responses")]

    calls.clear()
    responses.append({"username": "peer", "files": [{}, {}]})
    status.update(responseCount=1, fileCount=2)
    changed = auth.get(f"/api/soulseek/searches/{search_id}?since=v2:0:0:0")
    assert changed.json()["responses"] == responses
    assert changed.json()["revision"] == "v2:1:2:0"
    assert calls[-1] == ("GET", f"/searches/{search_id}/responses")


def test_search_revision_is_unavailable_when_response_snapshot_is_stale_or_invalid(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    status = {"responseCount": 1, "fileCount": 2, "lockedFileCount": 1}
    responses = []
    calls = []
    async def request(self, method, path, data=None):
        calls.append(path)
        return status if path == f"/searches/{search_id}" else responses
    monkeypatch.setattr(Soulseek, "request", request)

    # slskd can publish counters before its response endpoint catches up.
    empty = auth.get(f"/api/soulseek/searches/{search_id}?since=1:2:1")
    assert empty.json()["responses"] == []
    assert empty.json()["revision"] is None
    assert calls == [f"/searches/{search_id}", f"/searches/{search_id}/responses"]

    # Partial results cannot establish the counters either.
    responses[:] = [{"username": "peer", "files": [{}], "lockedFiles": []}]
    partial = auth.get(f"/api/soulseek/searches/{search_id}")
    assert partial.json()["responses"] == responses
    assert partial.json()["revision"] is None

    # Once the full snapshot arrives with matching counters, the revision is usable.
    responses[:] = [{"username": "peer", "files": [{}, {}], "lockedFiles": [{}]}]
    full = auth.get(f"/api/soulseek/searches/{search_id}")
    assert full.json()["responses"] == responses
    assert full.json()["revision"] == "v2:1:2:1"

    responses[:] = [{"username": "peer", "files": "malformed"}]
    malformed = auth.get(f"/api/soulseek/searches/{search_id}")
    assert malformed.json()["responses"] == responses
    assert malformed.json()["revision"] is None
    assert len(calls) == 8


def test_terminal_search_forces_response_fetch_even_when_revision_matches(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    calls = []
    responses = [{"username": "peer", "files": [{}]}]
    status = {"responseCount": 1, "fileCount": 1, "lockedFileCount": 0, "state": "Completed, TimedOut"}
    async def request(self, method, path, data=None):
        calls.append(path)
        return status if path == f"/searches/{search_id}" else responses
    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.get(f"/api/soulseek/searches/{search_id}?since=v2:1:1:0")
    assert response.json()["responses"] == responses
    assert response.json()["revision"] == "v2:1:1:0"
    assert calls == [f"/searches/{search_id}", f"/searches/{search_id}/responses"]

    # A terminal flag remains meaningful even when isComplete is omitted.
    calls.clear()
    status["state"] = "TimedOut, Completed"
    response = auth.get(f"/api/soulseek/searches/{search_id}?since=v2:1:1:0")
    assert response.json()["responses"] == responses
    assert calls == [f"/searches/{search_id}", f"/searches/{search_id}/responses"]

    # isComplete also forces the final fetch if the state field is absent.
    calls.clear()
    status.pop("state")
    status["isComplete"] = True
    response = auth.get(f"/api/soulseek/searches/{search_id}?since=v2:1:1:0")
    assert response.json()["responses"] == responses
    assert calls == [f"/searches/{search_id}", f"/searches/{search_id}/responses"]


def test_search_revision_missing_counters_disables_optimization(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    calls = []
    async def request(self, method, path, data=None):
        calls.append(path)
        return {"responseCount": 1, "fileCount": "2"} if path == f"/searches/{search_id}" else []
    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.get(f"/api/soulseek/searches/{search_id}?since=1:2:0")
    assert response.status_code == 200
    assert response.json()["revision"] is None
    assert response.json()["responses"] == []
    assert calls == [f"/searches/{search_id}", f"/searches/{search_id}/responses"]


def test_cancel_search_uses_upstream_put_and_delete_still_cleans_up(auth, monkeypatch):
    from app.integrations import Soulseek
    calls = []
    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {"ok": True}
    monkeypatch.setattr(Soulseek, "request", request)
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"

    assert auth.put(f"/api/soulseek/searches/{search_id}").json() == {"ok": True}
    assert calls[-1] == ("PUT", f"/searches/{search_id}", None)
    assert auth.delete(f"/api/soulseek/searches/{search_id}").json() == {"ok": True}
    assert calls[-1] == ("DELETE", f"/searches/{search_id}", None)


def test_unconfigured_slskd_is_clear_error(auth):
    response = auth.get("/api/soulseek/downloads")
    assert response.status_code == 503
    assert "SLSKD_API_KEY" in response.json()["detail"]


def test_cli_uses_same_auth_and_file_api(client, monkeypatch):
    monkeypatch.setenv("TEST_MMM_PASSWORD", "test-archive-password")
    client.headers["X-Requested-With"] = "meandmymusic"
    args = SimpleNamespace(command="ls", root="inbox", path="", password_env="TEST_MMM_PASSWORD")
    result = execute(args, client)
    assert result == {"path": ".", "entries": []}


def test_clear_downloads_uses_slskd_terminal_cleanup_without_touching_files(auth, settings, monkeypatch):
    from app.integrations import Soulseek
    calls = []
    downloaded = settings.inbox / 'Downloaded.flac'
    downloaded.write_bytes(b'archive file')
    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {}
    monkeypatch.setattr(Soulseek, 'request', request)
    assert auth.delete('/api/soulseek/downloads/completed').json() == {'ok':True}
    assert calls[-1] == ('DELETE', '/transfers/downloads/all/completed', None)
    assert downloaded.read_bytes() == b'archive file'
    key = 'f3782ee1-5cd2-45d8-936e-57fd18216b21'
    assert auth.delete(f'/api/soulseek/downloads/Peer%20Name/{key}').status_code == 200
    assert calls[-1][1] == f'/transfers/downloads/Peer%20Name/{key}?remove=false'
