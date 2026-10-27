import threading
import time
import wave

import pytest
from mediafile import MediaFile

from app.library_stats import LibraryStats


def track(root, name, seconds, artist=None, album=None, albumartist=None):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * int(8000 * seconds))
    media = MediaFile(path)
    media.artist, media.album, media.albumartist = artist, album, albumartist
    media.save()
    return path


def complete(stats, refresh=False):
    if refresh:
        with stats.lock:
            stats.checked = 0
    result = stats.get(refresh=refresh)
    for _ in range(300):
        if not result["scanning"]:
            assert not result["error"], result
            return result
        time.sleep(.01)
        result = stats.get()
    pytest.fail("Library inventory did not complete")


@pytest.fixture
def stats(settings):
    value = LibraryStats(settings)
    yield value
    value.close()


def test_duration_includes_existing_archive_and_groups_compilations_by_album_artist(settings, stats):
    one = track(settings.library, "A/One/01.wav", 2.25, "A", "One")
    track(settings.library, "A/One/02.wav", 1.5, "Guest", "One", "A")
    track(settings.library, "A/Two/01.wav", 1, "A", "Two")
    track(settings.library, "Various/01.wav", 2, "Singer 1", "Compilation", "Various Artists")
    track(settings.library, "Various/02.wav", 3, "Singer 2", "Compilation", "Various Artists")
    before = (one.read_bytes(), one.stat().st_mtime_ns)
    result = complete(stats)
    assert result["duration"] == 9.75
    assert result["tracks"] == 5 and result["albums"] == 3
    assert [a["name"] for a in result["artists"]] == ["Various Artists", "A"]
    assert result["artists"][1]["albums"][0] == {"name": "One", "duration": 3.75, "tracks": 2}
    assert before == (one.read_bytes(), one.stat().st_mtime_ns)
    assert not (settings.state / "beets.db").exists()


def test_invalid_hidden_symlink_inbox_and_unknown_tags(settings, stats):
    track(settings.library, "untagged.wav", .5)
    track(settings.inbox, "not-in-library.wav", 9)
    outside = track(settings.inbox, "outside.wav", 4)
    (settings.library / "outside.wav").symlink_to(outside)
    (settings.library / "linked-folder").symlink_to(settings.inbox, target_is_directory=True)
    track(settings.library, ".staging/hidden.wav", 5)
    (settings.library / "broken.flac").write_text("not audio")
    (settings.library / "cover.jpg").write_text("not audio")
    result = complete(stats)
    assert result["duration"] == .5 and result["tracks"] == 1
    assert result["skipped"] == 2  # Corrupt audio and excluded symlink.
    assert result["artists"][0]["name"] == "Unknown artist"
    assert result["artists"][0]["albums"][0]["name"] == "Unknown album"


def test_cache_survives_restart_and_updates_renames_edits_and_deletions(settings, monkeypatch):
    first = track(settings.library, "One.wav", 1, "A", "One")
    second = track(settings.library, "Two.wav", 2, "B", "Two")
    calls = []
    original = __import__("app.library_stats", fromlist=["MediaFile"]).MediaFile

    def read(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr("app.library_stats.MediaFile", read)
    stats = LibraryStats(settings)
    try:
        assert complete(stats)["tracks"] == 2
        assert sorted(calls) == ["One.wav", "Two.wav"]
        calls.clear()
        assert complete(stats, refresh=True)["duration"] == 3
        assert not calls
    finally:
        stats.close()
    stats = LibraryStats(settings)
    try:
        assert complete(stats)["duration"] == 3
        assert not calls
        media = MediaFile(first)
        media.albumartist = "Retagged"
        media.save()
        second.unlink()
        first.rename(settings.library / "Moved.wav")
        result = complete(stats, refresh=True)
        assert result["tracks"] == 1 and result["artists"][0]["name"] == "Retagged"
        assert calls == ["Moved.wav"]
    finally:
        stats.close()


def test_empty_and_unreadable_duration(settings, stats, monkeypatch):
    assert complete(stats)["ready"] and complete(stats)["duration"] == 0
    track(settings.library, "test.wav", 1)
    class BadDuration:
        length = float('nan')
    monkeypatch.setattr("app.library_stats.MediaFile", lambda _: BadDuration())
    result = complete(stats, refresh=True)
    assert result["duration"] == 0 and result["skipped"] == 1


def test_scan_runs_once_in_background_and_does_not_block_api(settings, auth, monkeypatch):
    track(settings.library, "One.wav", 1, "A", "One")
    entered, release = threading.Event(), threading.Event()
    original = __import__("app.library_stats", fromlist=["MediaFile"]).MediaFile
    calls = []

    def slow(path):
        calls.append(path)
        entered.set()
        assert release.wait(5)
        return original(path)

    monkeypatch.setattr("app.library_stats.MediaFile", slow)
    try:
        result = auth.get('/api/library/stats')
        assert result.status_code == 200 and result.json()["scanning"]
        assert entered.wait(2)
        assert auth.get('/api/library/stats').json()["scanning"]
        assert auth.post('/api/library/stats/refresh').json()["scanning"]
        assert auth.get('/api/health').status_code == 200
        assert len(calls) == 1
    finally:
        release.set()


def test_stats_requires_session_and_refresh_protection(client):
    assert client.get('/api/library/stats').status_code == 401
    assert client.post('/api/library/stats/refresh').status_code == 401
    client.headers['X-Requested-With'] = 'meandmymusic'
    client.post('/api/login', json={'password': 'test-archive-password'})
    del client.headers['X-Requested-With']
    assert client.post('/api/library/stats/refresh').status_code == 403
