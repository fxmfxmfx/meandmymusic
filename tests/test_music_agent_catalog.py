from app.integrations import search_releases
from app.music_agent_catalog import artist_albums
from app.music_agent_core import AgentError


def _id(number):
    return f"00000000-0000-0000-0000-{number:012d}"


def _release(number, *, artist="Он сказал поехали", title="Album", tracks=9,
             group=None, kind="Album", fmt="CD", status="Official"):
    return {"id": _id(number), "artist": artist, "title": title, "tracks": tracks,
            "release_group": _id(group) if group else "", "primary_type": kind,
            "format": fmt, "date": "2001", "status": status}


class API:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, path, *, params):
        self.calls.append((path, params))
        return self.pages[params["offset"] // params["limit"]]


def test_artist_catalog_paginates_and_deduplicates_editions_and_types():
    group = 999
    rows = [
        _release(1, title="Sexyswag", tracks=8, group=group, fmt="CD"),
        _release(2, title="Sexyswag", tracks=9, group=group, fmt="Digital Media"),
        _release(6, title="Sexyswag", tracks=9, group=group, fmt="CD"),
        _release(3, title="Он сказал поехали", tracks=8, group=998),
        _release(4, title="Single", tracks=1, kind="Single"),
        _release(5, title="Compilation", kind="Compilation"),
    ]
    # page size 2 forces the exact same artist query to continue through offsets.
    api = API([rows[:2], rows[2:4], rows[4:6], rows[6:]])
    result = artist_albums(api, "Он сказал поехали", page_size=2)
    assert [call[1] for call in api.calls] == [
        {"query": 'artist:"Он сказал поехали"', "limit": 2, "offset": 0},
        {"query": 'artist:"Он сказал поехали"', "limit": 2, "offset": 2},
        {"query": 'artist:"Он сказал поехали"', "limit": 2, "offset": 4},
        {"query": 'artist:"Он сказал поехали"', "limit": 2, "offset": 6},
    ]
    sexyswag = next(row for row in result if row["title"] == "Sexyswag")
    assert sexyswag["tracks"] == 9
    assert sexyswag["format"] == "Digital Media"
    assert len(sexyswag["editions"]) == 3
    assert {row["title"] for row in result} == {"Sexyswag", "Он сказал поехали"}


def test_artist_catalog_refuses_page_and_album_caps():
    full = [_release(i + 1, title=f"Album {i}", group=1000 + i) for i in range(2)]
    with __import__("pytest").raises(AgentError, match="page limit"):
        artist_albums(API([full, full]), "Он сказал поехали", max_pages=1, page_size=2)
    with __import__("pytest").raises(AgentError, match="more than 1"):
        artist_albums(API([full, []]), "Он сказал поехали", page_size=2, max_albums=1)


def test_artist_catalog_stops_on_repeated_raw_page_and_rejects_bad_collection():
    page = [_release(1, group=1), _release(2, group=2)]
    api = API([page, page])
    result = artist_albums(api, "Он сказал поехали", page_size=2)
    assert len(result) == 2
    assert len(api.calls) == 2
    with __import__("pytest").raises(AgentError, match="invalid artist catalog"):
        artist_albums(API([{"oops": True}]), "Он сказал поехали")


def test_search_releases_pagination_metadata_and_legacy_defaults(monkeypatch):
    import app.integrations as integrations
    calls = []

    class Response:
        content = b"yes"
        def raise_for_status(self): pass
        def json(self):
            return {"releases": [{"id": "release", "release-group": {"id": "group", "primary-type": "EP"}}]}

    monkeypatch.setattr(integrations.httpx, "get", lambda url, **kwargs: (calls.append(kwargs) or Response()))
    monkeypatch.setattr(integrations.time, "sleep", lambda _: None)
    row = search_releases("artist query")[0]
    assert calls[-1]["params"] == {"query": "artist query", "fmt": "json", "limit": 15}
    assert row["release_group"] == "group" and row["primary_type"] == "EP"
    search_releases('artist:"test"', offset=30, limit=7)
    assert calls[-1]["params"] == {"query": 'artist:"test"', "fmt": "json", "limit": 7, "offset": 30}
