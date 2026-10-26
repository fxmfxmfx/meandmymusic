from pathlib import Path
from types import SimpleNamespace
import wave

import pytest
from beets.autotag.hooks import AlbumInfo, TrackInfo
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

RELEASE = "4e9b3e98-6f40-4b9d-8765-6fa18dd73512"


@pytest.fixture
def settings(tmp_path):
    value = Settings(library=tmp_path / "music", inbox=tmp_path / "inbox", state=tmp_path / "state",
                     password="test-archive-password", slskd_key="", navidrome_url="")
    value.prepare()
    return value


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def auth(client):
    client.headers["X-Requested-With"] = "meandmymusic"
    assert client.post("/api/login", json={"password": "test-archive-password"}).status_code == 200
    return client


@pytest.fixture
def album(settings):
    directory = settings.inbox / "Test Album"
    directory.mkdir()
    for index in range(1, 3):
        with wave.open(str(directory / f"{index:02}.wav"), "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(8000)
            file.writeframes(b"\0\0" * 8000)
    return directory


@pytest.fixture
def match(monkeypatch):
    def fake_match(items, **kwargs):
        tracks = [TrackInfo(title=f"Track {i}", track_id=f"recording-{i}", release_track_id=f"track-{i}",
                            artist="Test Artist", artist_id="artist-id", artists=["Test Artist"], artists_ids=["artist-id"],
                            index=i, medium=1, medium_index=i, medium_total=len(items), length=1)
                  for i in range(1, len(items) + 1)]
        info = AlbumInfo(tracks=tracks, album="Test Album", album_id=RELEASE, artist="Test Artist", artist_id="artist-id",
                         artists=["Test Artist"], artists_ids=["artist-id"], year=2026, mediums=1, data_source="MusicBrainz")
        candidate = SimpleNamespace(info=info, mapping=dict(zip(items, tracks)), extra_items=[], extra_tracks=[], distance=.02)
        return "", "", SimpleNamespace(candidates=[candidate])
    monkeypatch.setattr("app.tagger.autotag.tag_album", fake_match)
    monkeypatch.setattr("app.tagger.time.sleep", lambda _: None)
    return fake_match
