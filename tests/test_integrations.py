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
