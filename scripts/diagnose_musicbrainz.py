#!/usr/bin/env python3
"""Read-only MusicBrainz search probe. No audio, credentials or file changes."""
import json
import os
import musicbrainzngs as mb

mb.set_useragent("meandmymusic-diagnostics", "0.4", "https://github.com/fxmfxmfx/meandmymusic")
mb.set_rate_limit(1.1)


def main():
    album = os.environ["DIAG_ALBUM"][:300]
    artist = os.environ.get("DIAG_ARTIST", "")[:300]
    tracks = json.loads(os.environ.get("DIAG_TRACKS", "[]"))
    if not isinstance(tracks, list) or any(not isinstance(t, str) for t in tracks):
        raise ValueError("Track titles must be a JSON array of strings")
    queries = [("release", {"release": album})]
    if artist:
        queries.append(("release", {"release": album, "artist": artist}))
    queries.extend(("recording", {"recording": title[:300]}) for title in tracks[:3])
    for entity, fields in queries:
        search = mb.search_releases if entity == "release" else mb.search_recordings
        result = search(strict=True, limit=25, **fields)
        entries = result.get(entity + "-list", [])
        print(json.dumps({"entity": entity, "query": fields, "total": result.get(entity + "-count"),
                          "results": [{key: entry[key] for key in
                                       ("id", "title", "artist-credit", "release-list", "medium-list", "date")
                                       if key in entry} for entry in entries]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
