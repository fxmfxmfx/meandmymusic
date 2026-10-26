import pytest


def test_auth_and_csrf(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/files").status_code == 401
    assert client.post("/api/login", json={"password": "test-archive-password"}).status_code == 403
    client.headers["X-Requested-With"] = "meandmymusic"
    assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"password": "test-archive-password"}).status_code == 200
    assert client.get("/api/files").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/files").status_code == 401


@pytest.mark.parametrize("path", ["../state/session.key", "/etc/passwd", "a/../../secret", "a\\..\\secret"])
def test_path_escape_rejected(auth, path):
    assert auth.get("/api/files", params={"path": path}).status_code == 400
    assert auth.post("/api/folders", json={"root": "library", "path": path}).status_code == 400


def test_symlinks_rejected(auth, settings):
    (settings.library / "outside").symlink_to(settings.state, target_is_directory=True)
    assert auth.get("/api/files", params={"path": "outside"}).status_code == 400
    assert auth.get("/api/files").json()["entries"] == []
    assert auth.post("/api/folders", json={"root": "library", "path": "outside/new"}).status_code == 400


def test_move_preserves_existing_files_and_roots(auth, settings):
    (settings.library / "one.flac").write_bytes(b"one")
    (settings.library / "two.flac").write_bytes(b"two")
    data = {"root": "library", "source": "one.flac", "destination": "two.flac"}
    assert auth.post("/api/move", json=data).status_code == 409
    assert (settings.library / "two.flac").read_bytes() == b"two"
    assert auth.post("/api/move", json={**data, "source": ""}).status_code == 400
    assert auth.post("/api/move", json={**data, "destination": ""}).status_code == 400


def test_exclusive_move_protects_a_destination_without_preflight_check(settings):
    from app.files import rename_noreplace
    source, destination = settings.library / "source", settings.library / "destination"
    source.write_bytes(b"original source")
    destination.write_bytes(b"external writer")
    with pytest.raises(FileExistsError):
        rename_noreplace(source, destination)
    assert source.read_bytes() == b"original source"
    assert destination.read_bytes() == b"external writer"


def test_mkdir_move_and_undo(auth, settings):
    assert auth.post("/api/folders", json={"root": "inbox", "path": "album"}).status_code == 200
    assert auth.post("/api/move", json={"root": "inbox", "source": "album", "destination": "renamed"}).status_code == 200
    events = auth.get("/api/history").json()
    assert auth.post(f"/api/history/{events[0]['id']}/undo").status_code == 200
    assert (settings.inbox / "album").is_dir()
    assert auth.post(f"/api/history/{events[1]['id']}/undo").status_code == 200
    assert not (settings.inbox / "album").exists()
    assert auth.post(f"/api/history/{events[1]['id']}/undo").status_code == 409


def test_nonempty_folder_undo_and_self_move_rejected(auth, settings):
    auth.post("/api/folders", json={"root": "library", "path": "album"})
    (settings.library / "album" / "song").write_text("song")
    event = auth.get("/api/history").json()[0]
    assert auth.post(f"/api/history/{event['id']}/undo").status_code == 409
    assert auth.post("/api/move", json={"root": "library", "source": "album", "destination": "album/sub"}).status_code == 400


def test_media_requires_auth_and_supported_extension(auth, settings):
    (settings.library / "private.txt").write_text("private")
    assert auth.get("/api/media", params={"root": "library", "path": "private.txt"}).status_code == 400
    assert auth.get("/api/files", params={"root": "invalid"}).status_code == 422


def test_cookie_and_response_headers(auth):
    response = auth.get("/api/session")
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    cookie = next(cookie for cookie in auth.cookies.jar if cookie.name == "mmm_session")
    assert cookie.has_nonstandard_attr("HttpOnly")


def test_settings_refuse_missing_password_or_nested_roots(tmp_path):
    from app.config import Settings
    with pytest.raises(RuntimeError, match="APP_PASSWORD"):
        Settings(password="").prepare()
    with pytest.raises(RuntimeError, match="non-nested"):
        Settings(password="long-enough-password", library=tmp_path, inbox=tmp_path / "inbox", state=tmp_path / "state").prepare()


@pytest.mark.parametrize("section", ["library", "inbox"])
def test_delete_file_and_nonempty_folder_in_both_roots(auth, settings, section):
    root = getattr(settings, section)
    (root / "single.flac").write_bytes(b"audio")
    folder = root / "album"
    folder.mkdir()
    (folder / "track.flac").write_bytes(b"audio")
    assert auth.request("DELETE", "/api/files", json={"root": section, "path": "single.flac"}).status_code == 200
    assert not (root / "single.flac").exists()
    assert auth.request("DELETE", "/api/files", json={"root": section, "path": "album"}).status_code == 200
    assert not folder.exists()
    event = auth.get("/api/history").json()[0]
    assert event["kind"] == "delete" and event["data"]["root"] == section
    assert auth.post(f"/api/history/{event['id']}/undo").status_code == 400


def test_delete_rejects_root_escape_and_top_level_symlink(auth, settings):
    (settings.state / "keep").write_text("keep")
    (settings.library / "outside").symlink_to(settings.state, target_is_directory=True)
    for path in ("", ".", "../state", "/etc", "outside"):
        assert auth.request("DELETE", "/api/files", json={"root": "library", "path": path}).status_code == 400
    assert (settings.state / "keep").read_text() == "keep"
    # A nested link is removed as a link; its target is never recursively deleted.
    (settings.library / "album").mkdir()
    (settings.library / "album/link").symlink_to(settings.state, target_is_directory=True)
    assert auth.request("DELETE", "/api/files", json={"root": "library", "path": "album"}).status_code == 200
    assert (settings.state / "keep").read_text() == "keep"
