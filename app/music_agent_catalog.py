"""Bounded, verified MusicBrainz artist catalog discovery for music-ai."""
from __future__ import annotations

from collections import Counter, defaultdict
from uuid import UUID

from app.music_agent_core import AgentError, normalize


def _valid_uuid(value):
    try:
        UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _track_count(release):
    value = release.get("tracks", release.get("track_count"))
    return value if type(value) is int and 1 <= value <= 300 else None


def _edition_key(release):
    # Prefer a broadly distributed digital edition, then stable early releases.
    fmt = str(release.get("format", "")).casefold()
    digital_rank = 0 if "digital media" in fmt else 1
    return digital_rank, str(release.get("date") or "9999"), str(release["id"])


def artist_albums(api, artist, *, max_pages=10, page_size=100, max_albums=50):
    """Return canonical verified albums/EPs; refuse results beyond safety caps."""
    if (not isinstance(artist, str) or not artist.strip() or
            type(max_pages) is not int or not 1 <= max_pages <= 100 or
            type(page_size) is not int or not 1 <= page_size <= 100 or
            type(max_albums) is not int or not 1 <= max_albums <= 500):
        raise AgentError("Artist catalog limits or artist name are invalid.")

    escaped = artist.strip().replace("\\", "\\\\").replace('"', '\\"')
    query = f'artist:"{escaped}"'
    all_rows, seen_ids, seen_pages = [], set(), set()
    for page in range(max_pages):
        try:
            rows = api.get("/api/musicbrainz/releases", params={
                "query": query, "limit": page_size, "offset": page * page_size,
            })
        except AgentError:
            raise
        except Exception:
            raise AgentError("MusicBrainz artist catalog could not be read. Check the manager connection and retry.") from None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise AgentError("MusicBrainz returned an invalid artist catalog. Retry the request.")
        page_ids = tuple(str(row.get("id", "")) for row in rows)
        if page_ids and page_ids in seen_pages:
            break
        seen_pages.add(page_ids)
        fresh = [row for row in rows if _valid_uuid(row.get("id")) and row["id"] not in seen_ids]
        for row in fresh:
            seen_ids.add(row["id"])
            all_rows.append(row)
        if len(rows) < page_size:
            break
        if page == max_pages - 1:
            raise AgentError(f"Artist catalog reached the {max_pages}-page limit. Narrow the artist name before starting downloads.")
        if not fresh:
            break

    eligible = []
    for row in all_rows:
        tracks = _track_count(row)
        kind = str(row.get("primary_type") or "").strip().casefold()
        status = str(row.get("status") or "").strip().casefold()
        if (not tracks or normalize(row.get("artist", "")) != normalize(artist) or
                (status and status != "official") or kind not in ("", "album", "ep")):
            continue
        if not isinstance(row.get("title"), str) or not row["title"].strip():
            continue
        eligible.append({**row, "tracks": tracks})

    groups = defaultdict(list)
    for row in eligible:
        group = row.get("release_group")
        key = f"group:{group}" if _valid_uuid(group) else f"title:{normalize(row['title'])}"
        groups[key].append(row)

    result = []
    for editions in groups.values():
        counts = Counter(row["tracks"] for row in editions)
        chosen_tracks = min(counts, key=lambda count: (-counts[count], count))
        matching = [row for row in editions if row["tracks"] == chosen_tracks]
        matching.sort(key=_edition_key)
        canonical = dict(matching[0])
        canonical["editions"] = sorted(editions, key=_edition_key)
        result.append(canonical)
    result.sort(key=lambda row: (normalize(row["title"]), str(row.get("release_group") or ""), str(row["id"])))

    if len(result) > max_albums:
        raise AgentError(f"Artist catalog contains more than {max_albums} albums or EPs. Narrow the artist name before starting downloads.")
    if not result:
        raise AgentError("No verified albums or EPs were found for this artist.")
    return result
