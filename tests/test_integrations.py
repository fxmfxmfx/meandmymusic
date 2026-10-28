from types import SimpleNamespace

from app.cli import execute


def test_soulseek_adapter_contract(auth, monkeypatch):
    from app.integrations import Soulseek
    calls = []
    async def request(self, method, path, data=None):
        calls.append((method, path, data))
        return {"id": "f3782ee1-5cd2-45d8-936e-57fd18216b21"}
    monkeypatch.setattr(Soulseek, "request", request)
    assert auth.post("/api/soulseek/searches", json={"query": "Test Artist"}).status_code == 200
    assert calls[-1][2]["searchText"] == "Test Artist"
    assert auth.post("/api/soulseek/downloads", json={"username": "user name", "files": [{"filename": "Music\\song.flac", "size": 123}]}).status_code == 200
    assert calls[-1] == ("POST", "/transfers/downloads/user%20name", [{"filename": "Music\\song.flac", "size": 123}])
    assert auth.post("/api/soulseek/downloads", json={"username": "user", "files": []}).status_code == 422


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
    async def request(self, method, path, data=None):
        calls.append((method, path))
        return {"responseCount": 2, "fileCount": 7, "lockedFileCount": 1} if path == f"/searches/{search_id}" else []
    monkeypatch.setattr(Soulseek, "request", request)

    response = auth.get(f"/api/soulseek/searches/{search_id}?since=2:7:1")
    assert response.status_code == 200
    assert response.json() == {
        "search": {"responseCount": 2, "fileCount": 7, "lockedFileCount": 1},
        "responses": None,
        "revision": "2:7:1",
    }
    assert calls == [("GET", f"/searches/{search_id}")]


def test_search_revision_fetches_full_responses_when_changed_or_initial(auth, monkeypatch):
    from app.integrations import Soulseek
    search_id = "f3782ee1-5cd2-45d8-936e-57fd18216b21"
    calls = []
    status = {"responseCount": 0, "fileCount": 0, "lockedFileCount": 0}
    responses = [{"username": "peer", "files": []}]
    async def request(self, method, path, data=None):
        calls.append((method, path))
        return status if path == f"/searches/{search_id}" else responses
    monkeypatch.setattr(Soulseek, "request", request)

    first = auth.get(f"/api/soulseek/searches/{search_id}")
    assert first.status_code == 200
    assert first.json()["responses"] == responses
    assert first.json()["revision"] == "0:0:0"
    assert calls == [("GET", f"/searches/{search_id}"), ("GET", f"/searches/{search_id}/responses")]

    calls.clear()
    status.update(responseCount=1, fileCount=2)
    changed = auth.get(f"/api/soulseek/searches/{search_id}?since=0:0:0")
    assert changed.json()["responses"] == responses
    assert changed.json()["revision"] == "1:2:0"
    assert calls[-1] == ("GET", f"/searches/{search_id}/responses")


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
