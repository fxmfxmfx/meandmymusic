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


def test_weak_artist_search_expands_exact_album_ids_and_scores_local_tracks(settings, album, match, monkeypatch):
    from types import SimpleNamespace
    target_id = "cfb473ef-d4e9-4b6f-8a31-f674450e9241"
    calls = []
    original = match
    def matcher(items, **kwargs):
        calls.append(kwargs)
        a, b, proposal = original(items, **kwargs)
        candidate = proposal.candidates[0]
        if "search_ids" in kwargs:
            candidate.info.album_id = target_id
            candidate.info.album = "Wanted Album"
            candidate.info.artist = "Alternate Artist Credit"
            candidate.distance = .02
        else:
            candidate.info.album = "Wrong Album"
            candidate.distance = .6
        return a, b, SimpleNamespace(candidates=[candidate])
    monkeypatch.setattr("app.tagger.autotag.tag_album", matcher)
    monkeypatch.setattr("app.tagger.discover_release_ids", lambda title, items=(): [target_id])
    before = {p.name: fingerprint(p) for p in album.iterdir()}
    result = match_album(settings, "Test Album", "Tagged Artist", "Wanted Album")
    assert calls[-1] == {"search_ids": [target_id]}
    assert result["candidates"][0]["id"] == target_id
    assert result["candidates"][0]["artist"] == "Alternate Artist Credit"
    assert result["recommended_release_id"] == target_id
    assert {p.name: fingerprint(p) for p in album.iterdir()} == before


def test_title_discovery_is_strict_but_does_not_lock_artist_or_track_count(monkeypatch):
    from app.tagger import discover_release_ids
    calls = []
    def search(**kwargs):
        calls.append(kwargs)
        return {"release-list": [{"id": RELEASE, "title": "Wanted Album"},
                                 {"id": RELEASE, "title": "WANTED ALBUM"},
                                 {"id": "wrong", "title": "Not Wanted Album"}]}
    monkeypatch.setattr("app.tagger.musicbrainzngs.search_releases", search)
    assert discover_release_ids("Wanted Album") == [RELEASE]
    assert calls == [{"release": "Wanted Album", "strict": True, "limit": 25}]


def test_incomplete_or_ambiguous_match_does_not_recommend_automatic_preview(settings, album, match, monkeypatch):
    from types import SimpleNamespace
    def matcher(items, **kwargs):
        a, b, proposal = match(items, **kwargs)
        candidate = proposal.candidates[0]
        candidate.extra_tracks = [object()]
        return a, b, SimpleNamespace(candidates=[candidate])
    monkeypatch.setattr("app.tagger.autotag.tag_album", matcher)
    assert match_album(settings, "Test Album")["recommended_release_id"] is None


def test_title_search_failure_preserves_initial_candidates(settings, album, match, monkeypatch):
    import musicbrainzngs
    def fail(title, items=()):
        raise musicbrainzngs.NetworkError("test failure")
    monkeypatch.setattr("app.tagger.discover_release_ids", fail)
    result = match_album(settings, "Test Album", "Artist", "Different Album")
    assert result["candidates"] and result["warnings"]
    assert result["recommended_release_id"] is None


def test_track_search_finds_shared_release_without_artist_or_album_match(monkeypatch):
    from types import SimpleNamespace
    from app.tagger import discover_release_ids
    monkeypatch.setattr("app.tagger.musicbrainzngs.search_releases", lambda **kwargs: {"release-list": []})
    calls = []
    def search(**kwargs):
        calls.append(kwargs)
        title = kwargs["recording"]
        return {"recording-list": [{"title": title, "release-list": [
            {"id": RELEASE, "title": "Different Album Credit"},
            {"id": "unrelated-" + title, "title": "Compilation"}]},
            {"title": title, "release-list": [{"id": "unrelated-" + title}]}]}
    monkeypatch.setattr("app.tagger.musicbrainzngs.search_recordings", search)
    items = [SimpleNamespace(title=title) for title in ["Opening Song", "Middle Song", "Closing Song"]]
    assert discover_release_ids("Wrong Album Tag", items) == [RELEASE]
    assert len(calls) == 3
    assert all(set(call) == {"recording", "strict", "limit"} for call in calls)


def test_competing_complete_high_scoring_editions_require_review(settings, album, match, monkeypatch):
    from types import SimpleNamespace
    def matcher(items, **kwargs):
        a, b, proposal = match(items, **kwargs)
        first = proposal.candidates[0]
        _, _, other = match(items, **kwargs)
        second = other.candidates[0]
        second.info.album_id = "cfb473ef-d4e9-4b6f-8a31-f674450e9241"
        second.distance = .03
        return a, b, SimpleNamespace(candidates=[first, second])
    monkeypatch.setattr("app.tagger.autotag.tag_album", matcher)
    assert match_album(settings, "Test Album")["recommended_release_id"] is None
