import httpx
import pytest

from app.music_agent_core import (AgentError, MusicAPI, discover_inbox, inspect_preview,
                                  normalize, release_candidates, search_sources, transfer_status)


def test_normalize_and_exact_release_validation():
    assert normalize("Beyoncé — Café") == "beyonce cafe"
    assert normalize("Земфира") and normalize("Земфира") != normalize("Zemfira")
    class API:
        def get(self, *a, **k):
            return [{"id":"00000000-0000-0000-0000-000000000001", "artist":"Artist", "title":"Album", "tracks":10},
                    {"id":"00000000-0000-0000-0000-000000000002", "artist":"Other", "title":"Album", "tracks":10},
                    {"id":"00000000-0000-0000-0000-000000000003", "artist":"Artist", "title":"Album", "tracks":301},
                    {"id":"00000000-0000-0000-0000-000000000004", "artist":"Artist", "title":"Album", "tracks":True}]
    assert [x["id"] for x in release_candidates(API(), "Artist", "Album")] == ["00000000-0000-0000-0000-000000000001"]

    class ArtistOnly:
        def get(self, path, params):
            assert params["query"] == 'artist:"Artist"'
            return [{"id":"00000000-0000-0000-0000-000000000001", "artist":"Artist", "title":"Album", "tracks":10}]
    assert len(release_candidates(ArtistOnly(), "Artist", "")) == 1


def test_groups_remote_paths_and_does_not_count_non_flac_or_conflicts():
    release = {"id":"r", "artist":"Artist", "title":"Album", "tracks":2}
    class API:
        def post(self, *a, **k): return {"id":"s"}
        def get(self, *a, **k):
            files = [{"filename":"Artist - Album\\CD1\\01.flac", "size":100},
                     {"filename":"Artist - Album\\Disc 02\\02.FLAC", "size":200},
                     {"filename":"Artist - Album\\cover.jpg", "size":999}]
            return {"search":{"isComplete":True,"responseCount":1,"fileCount":6,"lockedFileCount":0},
                    "responses":[{"username":"peer", "hasFreeUploadSlot":True, "files":files + files}]}
    found = search_sources(API(), release, wait=0.5)
    assert len(found) == 1
    assert found[0]["directory"] == "Artist - Album"
    assert [x["filename"] for x in found[0]["files"]] == ["Artist - Album\\CD1\\01.flac", "Artist - Album\\Disc 02\\02.FLAC"]
    assert found[0]["free_slots"] is True
    assert found[0]["total_bytes"] == 300


def test_search_waits_for_terminal_counters_to_match_delivered_snapshot():
    class API:
        reads = 0
        def post(self, *a, **k): return {"id":"s"}
        def get(self, *a, **k):
            self.reads += 1
            responses = [{"username":"p", "files":[]}] if self.reads > 1 else []
            return {"search":{"isComplete":True,"responseCount":1,"fileCount":0,"lockedFileCount":0},"responses":responses}
    api = API()
    assert search_sources(api, {"id":"r","artist":"A","title":"B","tracks":1}, wait=1) == []
    assert api.reads > 1


def test_cyrillic_search_does_not_send_empty_ascii_fallback():
    class API:
        queries = []
        def post(self, path, json):
            self.queries.append(json['query'])
            return {'id': 'search'}
        def get(self, path):
            return {'search': {'isComplete': True, 'responseCount': 0, 'fileCount': 0, 'lockedFileCount': 0}, 'responses': []}
    api = API()
    assert search_sources(api, {'artist': 'Земфира', 'title': 'Вендетта', 'tracks': 15}) == []
    assert api.queries == ['Земфира Вендетта']


@pytest.mark.parametrize("state,expected", [("Completed, Errored", "failed"), ("Cancelled, Succeeded", "failed"), ("Completed, TimedOut", "failed"), ("Completed, Rejected", "failed"), ("Completed", "waiting"), ("Completed, Succeeded", "complete")])
def test_transfer_terminal_success_requires_all_bytes(state, expected):
    source = {"username":"peer", "files":[{"filename":"A/1.flac", "size":10}]}
    class API:
        def get(self, *a): return [{"username":"peer", "directories":[{"files":[{"filename":"A/1.flac", "size":10, "state":state, "bytesTransferred":10}]}]}]
    result = transfer_status(API(), source)
    assert result["status"] == expected
    assert result["found"] == 1


def test_inbox_discovery_rejects_ambiguity_and_wrong_size():
    source = {"directory":"Album", "files":[{"filename":"Album/1.flac", "size":10}]}
    class API:
        def __init__(self, entries): self.entries=entries
        def get(self, *a, **k): return {"entries":self.entries}
    with pytest.raises(AgentError): discover_inbox(API([{"name":"1.flac","path":"One/1.flac","size":10},{"name":"1.flac","path":"Two/1.flac","size":10}]), source)
    with pytest.raises(AgentError): discover_inbox(API([]), source)


def test_inbox_discovery_keeps_disc_paths():
    source = {"directory":"Artist - Album", "files":[{"filename":"Artist - Album\\CD1\\1.flac", "size":10},{"filename":"Artist - Album\\CD2\\2.flac", "size":20}]}
    class API:
        def get(self, path, params):
            current = params["path"]
            if current == "": return {"entries":[{"path":"Artist - Album","directory":True}]}
            if current == "Artist - Album": return {"entries":[{"path":"Artist - Album/CD1","directory":True},{"path":"Artist - Album/CD2","directory":True}]}
            if current.endswith("CD1"): return {"entries":[{"name":"1.flac","path":"Artist - Album/CD1/1.flac","size":10,"audio":True}]}
            return {"entries":[{"name":"2.flac","path":"Artist - Album/CD2/2.flac","size":20,"audio":True}]}
    assert discover_inbox(API(), source) == "Artist - Album"


def test_preview_must_be_copy_without_cleanup_and_match_edition():
    release = {"id":"r", "artist":"Artist", "title":"Album"}
    source = {"directory":"Album", "files":[{"filename":"Album/1.flac","size":10}]}
    good = {"status":"ready", "result":{"delete_source":False,"mode":"copy","source":"Inbox/Album","release_id":"r","created":__import__("time").time(),"rows":[{"source":"Inbox/Album/1.flac","destination":"Artist/Album/1.flac","fingerprint":{"size":10},"after":{"albumartist":"Artist","album":"Album","mb_albumid":"r"}}],"artist":"Artist","album":"Album","confidence":99}}
    assert inspect_preview(good, source, "Inbox/Album", release) == good
    for key,value in (("delete_source",True),("release_id","other")):
        bad = {"status":"ready","result":dict(good["result"], **{key:value})}
        with pytest.raises(AgentError): inspect_preview(bad, source, "Inbox/Album", release)


def test_http_failure_never_exposes_upstream_body_or_password():
    def handler(request):
        assert request.headers.get("x-requested-with") == "meandmymusic"
        if request.url.path == "/api/login": return httpx.Response(200, json={"ok":True})
        return httpx.Response(500, text="secret password upstream traceback")
    api = MusicAPI("http://manager", "secret", transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(AgentError) as error: api.get("/api/files")
        assert "secret" not in str(error.value) and "traceback" not in str(error.value)
    finally: api.close()


def test_transient_reads_retry_but_queue_is_never_repeated(monkeypatch):
    from app import music_agent_core as core
    monkeypatch.setattr(core.time, "sleep", lambda seconds: None)
    attempts = {"read": 0, "queue": 0}
    def handler(request):
        if request.url.path == "/api/login":
            return httpx.Response(200, json={"ok": True})
        if request.method == "GET":
            attempts["read"] += 1
            return httpx.Response(503 if attempts["read"] == 1 else 200, json=[])
        attempts["queue"] += 1
        raise httpx.ReadTimeout("private upstream information", request=request)
    api = MusicAPI("http://manager", "secret", transport=httpx.MockTransport(handler))
    try:
        assert api.get("/api/musicbrainz/releases") == []
        assert attempts["read"] == 2
        with pytest.raises(AgentError):
            api.post("/api/soulseek/downloads", json={})
        assert attempts["queue"] == 1
    finally:
        api.close()
