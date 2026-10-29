import httpx
import pytest

from app.music_agent_core import (AgentError, SearchUnavailable, MusicAPI, discover_inbox, inspect_preview,
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
    with pytest.raises(SearchUnavailable):
        search_sources(api, {'artist': 'Земфира', 'title': 'Вендетта', 'tracks': 15})
    assert api.queries == ['Земфира Вендетта', 'Вендетта']


def test_album_only_extended_search_recovers_complete_source_and_keeps_validation():
    release = {'artist': 'Overmono', 'title': 'Cash Romantic', 'tracks': 2}
    class API:
        def __init__(self): self.calls = []
        def post(self, path, json):
            self.calls.append((json['query'], json['mode']))
            return {'id': str(len(self.calls))}
        def get(self, path):
            query = self.calls[-1][0]
            if query != 'Cash Romantic': responses = []
            else:
                responses = [
                    {'username': 'right', 'files': [
                        {'filename': 'Overmono - Cash Romantic/01.flac', 'size': 10},
                        {'filename': 'Overmono - Cash Romantic/02.flac', 'size': 20}]},
                    {'username': 'wrong-album', 'files': [
                        {'filename': 'Overmono - Other/01.flac', 'size': 10},
                        {'filename': 'Overmono - Other/02.flac', 'size': 20}]},
                ]
            files = sum(len(peer['files']) for peer in responses)
            return {'search': {'isComplete': True, 'responseCount': len(responses), 'fileCount': files,
                               'lockedFileCount': 0}, 'responses': responses}
    api = API()
    found = search_sources(api, release, wait=0.5)
    assert api.calls == [('Overmono Cash Romantic', 'standard'), ('Cash Romantic', 'extended')]
    assert [source['username'] for source in found] == ['right']


def test_nonzero_responses_without_valid_album_return_empty_and_report_checks():
    class API:
        def post(self, path, json): return {'id': 's'}
        def get(self, path):
            peer = {'username': 'peer', 'files': [{'filename': 'Other Album/01.flac', 'size': 10}]}
            return {'search': {'isComplete': True, 'responseCount': 1, 'fileCount': 1, 'lockedFileCount': 0},
                    'responses': [peer]}
    assert search_sources(API(), {'artist': 'Artist', 'title': 'Album', 'tracks': 1}, wait=0.5) == []


def test_empty_fallback_sequence_is_bounded_and_reports_queries():
    class API:
        calls = []
        def post(self, path, json):
            self.calls.append((json['query'], json['mode']))
            return {'id': str(len(self.calls))}
        def get(self, path):
            return {'search': {'isComplete': True, 'responseCount': 0, 'fileCount': 0, 'lockedFileCount': 0},
                    'responses': []}
    api = API()
    with pytest.raises(SearchUnavailable) as error:
        search_sources(api, {'artist': 'Beyoncé', 'title': 'Café', 'tracks': 1}, wait=0.5)
    assert len(api.calls) == 3
    assert [item['query'] for item in error.value.details] == ['Beyoncé Café', 'Beyonce Cafe', 'Café']


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


def test_artist_batch_accepts_only_a_complete_recommended_edition_of_same_album():
    from app.music_agent_core import get_match
    selected='00000000-0000-0000-0000-000000000001';recommended='00000000-0000-0000-0000-000000000002'
    release={'id':selected,'artist':'Artist','title':'Album','tracks':1,'accept_recommended_edition':True}
    candidate={'id':recommended,'artist':'Artist','title':'Album','tracks':1,'missing_tracks':0,'extra_files':0,'confidence':99}
    class API:
        def post(self,*args,**kwargs):return {'recommended_release_id':recommended,'candidates':[candidate]}
    assert get_match(API(),'Inbox',release)['recommended_release_id']==recommended
    with pytest.raises(AgentError):get_match(API(),'Inbox',{**release,'accept_recommended_edition':False})
    candidate['artist']='Other artist'
    with pytest.raises(AgentError):get_match(API(),'Inbox',release)


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
