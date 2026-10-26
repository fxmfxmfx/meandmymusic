import json
import time

import pytest
from beets.library import Item

from app.tagger import apply_import, fingerprint, library, preview, match_album
from conftest import RELEASE


def test_real_beets_preview_and_import(settings, album, match):
    original = {p.name: fingerprint(p) for p in album.iterdir()}
    plan = preview(settings, "Test Album", RELEASE)
    json.dumps(plan)
    assert plan["confidence"] == 98
    assert len(plan["rows"]) == 2
    assert plan["rows"][0]["destination"].startswith("Test Artist/2026 - Test Album/")
    assert not list(settings.library.rglob("*.wav"))
    result = apply_import(settings, plan)
    assert result["files"] == 2
    assert {p.name: fingerprint(p) for p in album.iterdir()} == original
    for row in plan["rows"]:
        item = Item.from_path(str(settings.library / row["destination"]))
        assert item.album == "Test Album"
        assert item.artist == "Test Artist"
        assert item.mb_albumid == RELEASE
        assert item.title == row["after"]["title"]
    assert len(list(library(settings).items())) == 2
    assert not list(settings.library.rglob(".mmm-*"))
    with pytest.raises(ValueError, match="exists"):
        apply_import(settings, plan)


def test_changed_source_prevents_any_write(settings, album, match):
    plan = preview(settings, "Test Album", RELEASE)
    with (album / "02.wav").open("ab") as file:
        file.write(b"changed")
    with pytest.raises(ValueError, match="changed"):
        apply_import(settings, plan)
    assert not list(settings.library.rglob("*.wav"))


def test_failed_write_cleans_up_staging(settings, album, match, monkeypatch):
    plan = preview(settings, "Test Album", RELEASE)
    original = Item.write
    calls = 0
    def fail_second(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated disk failure")
        return original(self, **kwargs)
    monkeypatch.setattr(Item, "write", fail_second)
    with pytest.raises(OSError, match="simulated"):
        apply_import(settings, plan)
    assert not list(settings.library.rglob("*.wav"))
    assert not list(settings.library.rglob(".mmm-*"))
    assert len(list(album.glob("*.wav"))) == 2


def test_destination_publication_never_overwrites(settings, album, match, monkeypatch):
    import os
    plan = preview(settings, "Test Album", RELEASE)
    link = os.link
    count = 0
    def competing_file(src, dst):
        nonlocal count
        count += 1
        if count == 2:
            dst.write_bytes(b"another writer")
        link(src, dst)
    monkeypatch.setattr("app.tagger.os.link", competing_file)
    with pytest.raises(FileExistsError):
        apply_import(settings, plan)
    assert not (settings.library / plan["rows"][0]["destination"]).exists()
    assert (settings.library / plan["rows"][1]["destination"]).read_bytes() == b"another writer"


def test_stale_preview_rejected(settings, album, match):
    plan = preview(settings, "Test Album", RELEASE)
    plan["created"] = time.time() - 3601
    with pytest.raises(ValueError, match="over an hour"):
        apply_import(settings, plan)


def wait_job(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(.02)
    raise AssertionError("Job did not finish")


def test_preview_apply_once_and_catalog_follows_rename(auth, settings, album, match):
    response = auth.post("/api/imports/preview", json={"path": "Test Album", "release_id": RELEASE})
    assert response.status_code == 200
    plan = wait_job(auth, response.json()["id"])
    assert plan["status"] == "ready", plan
    job = auth.post(f"/api/imports/{plan['id']}/apply")
    assert job.status_code == 200
    assert auth.post(f"/api/imports/{plan['id']}/apply").status_code == 409
    result = wait_job(auth, job.json()["id"])
    assert result["status"] == "done", result
    assert auth.post("/api/move", json={"root": "library", "source": "Test Artist", "destination": "Renamed Artist"}).status_code == 200
    assert all(item.filepath.is_relative_to(settings.library / "Renamed Artist") for item in library(settings).items())


def test_delete_imported_folder_cleans_catalog(auth, settings, album, match):
    apply_import(settings, preview(settings, "Test Album", RELEASE))
    assert len(list(library(settings).items())) == 2
    response = auth.request("DELETE", "/api/files", json={"root": "library", "path": "Test Artist"})
    assert response.status_code == 200
    assert not list(library(settings).items())
    assert not (settings.library / "Test Artist").exists()
    assert len(list(album.glob("*.wav"))) == 2


def test_matching_uses_embedded_metadata_and_release_id_without_modifying_files(settings, album, match, monkeypatch):
    for index, path in enumerate(sorted(album.iterdir()), 1):
        item = Item.from_path(str(path))
        item.artist = "Tagged Artist"
        item.albumartist = "Tagged Album Artist"
        item.album = "Tagged Album"
        item.title = f"Tagged Track {index}"
        item.mb_albumid = RELEASE
        item.write()
    before = {p.name: fingerprint(p) for p in album.iterdir()}
    calls = []
    def capture(items, **kwargs):
        calls.append((items, kwargs))
        return match(items, **kwargs)
    monkeypatch.setattr("app.tagger.autotag.tag_album", capture)
    result = match_album(settings, "Test Album")
    assert result["artist"] == "Tagged Album Artist"
    assert result["album"] == "Tagged Album" and result["has_tags"]
    assert calls[0][1] == {"search_artist": "Tagged Album Artist", "search_album": "Tagged Album", "search_ids": [RELEASE]}
    assert calls[0][0][0].title == "Tagged Track 1"
    assert result["candidates"][0]["confidence"] == 98
    assert result["candidates"][0]["tracks"] == 2
    assert {p.name: fingerprint(p) for p in album.iterdir()} == before
    result = match_album(settings, "Test Album", "Corrected Artist", "Corrected Album")
    assert calls[-1][1] == {"search_artist": "Corrected Artist", "search_album": "Corrected Album"}


def test_match_endpoint_returns_ranked_candidates_and_empty_folder_error(auth, settings, album, match):
    response = auth.post("/api/musicbrainz/match", json={"path": "Test Album"})
    assert response.status_code == 200, response.text
    assert response.json()["has_tags"] is False
    assert response.json()["candidates"][0]["id"] == RELEASE
    (settings.inbox / "empty").mkdir()
    assert auth.post("/api/musicbrainz/match", json={"path": "empty"}).status_code == 400
    assert auth.post("/api/musicbrainz/match", json={"path": "../state"}).status_code == 400
