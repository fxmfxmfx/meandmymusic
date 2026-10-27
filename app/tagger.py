"""Beets matches and writes metadata; imports always copy and keep the inbox intact."""
import hashlib
import json
import os
import shutil
import tempfile
import time
import unicodedata
from collections import Counter
from pathlib import Path

from beets import autotag
from beets.library import Item, Library
import musicbrainzngs

from app.files import AUDIO, operation_lock, safe_path
from app.integrations import musicbrainz_lock
from app.inbox import PROVENANCE, snapshot
from app.proxy import beets_proxy

PATH_FORMAT = "$albumartist/$year - $album/$disc-$track $title"
SUMMARY_FIELDS = ("title", "artist", "album", "albumartist", "year", "track", "tracktotal", "disc", "disctotal", "mb_albumid")


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"size": path.stat().st_size, "sha256": digest.hexdigest()}


def library(settings):
    return Library(str(settings.state / "beets.db"), directory=str(settings.library), path_formats=[("default", PATH_FORMAT)])


def album_items(settings, source):
    folder = safe_path(settings.inbox, source, allow_root=False)
    if not folder.is_dir():
        raise ValueError("Select an album folder in Inbox")
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO and p.is_file())
    if not paths or len(paths) > 300:
        raise ValueError("Select an album folder containing 1–300 audio files")
    return [Item.from_path(str(safe_path(settings.inbox, p.relative_to(settings.inbox).as_posix()))) for p in paths]


def normalized_title(value):
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def discover_release_ids(album, items=()):
    """Widen a weak match without assuming the artist credit in local tags is right.

    Called inside musicbrainz_lock/beets_proxy: reuse rate limiting, user agent
    and SOCKS transport. Fetch full tracklists through beets only for exact titles.
    """
    ids, errors = [], []
    try:
        result = musicbrainzngs.search_releases(release=album, strict=True, limit=25)
        title = normalized_title(album)
        ids = list(dict.fromkeys(release["id"] for release in result.get("release-list", [])
                                if release.get("id") and normalized_title(release.get("title", "")) == title))
    except musicbrainzngs.MusicBrainzError as error:
        errors.append(error)
    titles = list(dict.fromkeys(item.title.strip() for item in items
                               if item.title and any(c.isalpha() for c in item.title)
                               and item.title.casefold() not in {"intro", "outro", "interlude"}))
    # Sample across the album, then score every local track against each candidate.
    sample = list(dict.fromkeys(titles[index] for index in (0, len(titles)//2, len(titles)-1))) if titles else []
    votes = Counter()
    for track_title in sample:
        try:
            result = musicbrainzngs.search_recordings(recording=track_title, strict=True, limit=10)
            found = {release["id"] for recording in result.get("recording-list", [])
                     if normalized_title(recording.get("title", "")) == normalized_title(track_title)
                     for release in recording.get("release-list", []) if release.get("id")}
            # Duplicate recordings/editions from one query count as one track vote.
            votes.update(found)
        except musicbrainzngs.MusicBrainzError as error:
            errors.append(error)
    supported = [release_id for release_id, count in votes.most_common()
                 if count >= min(2, len(sample))]
    overlap = [release_id for release_id in ids if release_id in supported]
    result_ids = list(dict.fromkeys(overlap + supported[:6] + ids[:6] + supported + ids))[:12]
    if not result_ids and errors:
        raise errors[0]
    return result_ids


def match_album(settings, source, artist=None, album=None):
    items = album_items(settings, source)
    def common(field):
        values = [str(item.get(field) or "").strip() for item in items]
        return Counter(value for value in values if value).most_common(1)[0][0] if any(values) else ""
    tagged_artist, tagged_album = common("albumartist") or common("artist"), common("album")
    search_artist = tagged_artist if artist is None else artist.strip()
    search_album = tagged_album if album is None else album.strip()
    # Missing tags may suggest a folder name, but never masquerade as metadata.
    if not search_album:
        search_album = Path(source).name.replace("_", " ")
    ids = {item.mb_albumid for item in items if item.mb_albumid}
    kwargs = {"search_artist": search_artist or None, "search_album": search_album}
    if artist is None and album is None and len(ids) == 1:
        kwargs["search_ids"] = list(ids)
    warnings = []
    with musicbrainz_lock:
        try:
            with beets_proxy(settings.outbound_proxy):
                _, _, proposal = autotag.tag_album(items, **kwargs)
                matches = {match.info.album_id: match for match in proposal.candidates}
                best = min(matches.values(), key=lambda m: float(m.distance), default=None)
                # Beets' default search only returns a few fuzzy results and an
                # existing MBID can restrict it to the wrong edition entirely.
                if best is None or float(best.distance) > .10 or normalized_title(best.info.album) != normalized_title(search_album):
                    try:
                        ids = [release_id for release_id in discover_release_ids(search_album, items) if release_id not in matches]
                        if ids:
                            _, _, expanded = autotag.tag_album(items, search_ids=ids)
                            matches.update((match.info.album_id, match) for match in expanded.candidates)
                    except musicbrainzngs.MusicBrainzError:
                        warnings.append("Additional album/track search is unavailable. Results may be incomplete.")
        finally:
            time.sleep(1.1)
    candidates = []
    ordered = sorted(matches.values(), key=lambda candidate: float(candidate.distance))
    for match in ordered[:20]:
        info = match.info
        candidates.append({"id": info.album_id, "artist": info.artist, "title": info.album,
                           "date": str(info.year or ""), "country": info.country or "",
                           "format": info.media or "", "tracks": len(info.tracks),
                           "disambiguation": info.albumdisambig or "",
                           "confidence": round(max(0, 1 - float(match.distance)) * 100, 1),
                           "exact_title": normalized_title(info.album) == normalized_title(search_album),
                           "extra_files": len(match.extra_items), "missing_tracks": len(match.extra_tracks)})
    recommended = None
    if candidates:
        top = candidates[0]
        clear_lead = len(candidates) == 1 or top["confidence"] - candidates[1]["confidence"] >= 10
        if top["confidence"] >= 95 and top["exact_title"] and not top["extra_files"] and not top["missing_tracks"] and clear_lead:
            recommended = top["id"]
    return {"artist": search_artist, "album": search_album, "tracks": len(items),
            "has_tags": bool(tagged_artist or tagged_album), "candidates": candidates,
            "recommended_release_id": recommended, "warnings": warnings}


def remove_catalog_paths(settings, source):
    if not (settings.state / "beets.db").exists():
        return
    lib = library(settings)
    with lib.transaction():
        for item in lib.items():
            path = Path(os.fsdecode(item.path))
            if path == source or path.is_relative_to(source):
                item.remove(delete=False)


def preview(settings, source, release_id):
    folder = safe_path(settings.inbox, source, allow_root=False)
    if not folder.is_dir():
        raise ValueError("Select a single album folder in Inbox to import")
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO and p.is_file())
    if not paths or len(paths) > 300:
        raise ValueError("Select an album folder containing 1–300 audio files")
    # Beets needs persisted item/album IDs to expand path templates. Keep this
    # preview catalog entirely in memory: no changes to the real beets catalog.
    lib = Library(":memory:", directory=str(settings.library), path_formats=[("default", PATH_FORMAT)])
    items, originals = [], {}
    for path in paths:
        path = safe_path(settings.inbox, path.relative_to(settings.inbox).as_posix())
        item = Item(lib, **dict(Item.from_path(str(path))))
        items.append(item)
        originals[id(item)] = {key: item.get(key) for key in SUMMARY_FIELDS}
    with musicbrainz_lock:
        try:
            with beets_proxy(settings.outbound_proxy):
                _, _, proposal = autotag.tag_album(items, search_ids=[release_id])
        finally:
            time.sleep(1.1)
    if not proposal.candidates:
        raise ValueError("beets could not match the files to this release")
    match = proposal.candidates[0]
    if match.extra_items or match.extra_tracks:
        raise ValueError(f"Incomplete match: extra files {len(match.extra_items)}, missing tracks {len(match.extra_tracks)}. Choose another edition or complete the album.")
    autotag.apply_metadata(match.info, match.mapping)
    lib.add_album(items)
    rows, targets = [], set()
    for item in items:
        path = Path(os.fsdecode(item.path))
        destination = Path(os.fsdecode(item.destination()))
        relative = destination.relative_to(settings.library).as_posix()
        safe_path(settings.library, relative, exists=False, allow_root=False)
        if destination.exists() or relative in targets:
            raise ValueError(f"Destination already exists or is duplicated: {relative}")
        targets.add(relative)
        metadata = {key: item.get(key) for key in item._media_fields if key in item}
        rows.append({"source": path.relative_to(settings.inbox).as_posix(), "destination": relative,
                     "fingerprint": fingerprint(path), "before": originals[id(item)],
                     "after": {key: item.get(key) for key in SUMMARY_FIELDS}, "metadata": metadata})
    return {"source": source, "release_id": release_id, "artist": match.info.artist, "album": match.info.album,
            "confidence": round((1 - float(match.distance)) * 100, 1), "rows": rows,
            "created": time.time(), "mode": "copy",
            "inbox_snapshot": snapshot(settings, source, audio_only=False)[0]}


def apply_import(settings, plan):
    with operation_lock:
        if time.time() - plan["created"] > 3600:
            raise ValueError("This preview is over an hour old. Create a new one.")
        checked = []
        for row in plan["rows"]:
            src = safe_path(settings.inbox, row["source"], allow_root=False)
            dst = safe_path(settings.library, row["destination"], exists=False, allow_root=False)
            if fingerprint(src) != row["fingerprint"]:
                raise ValueError(f"Source file changed: {row['source']}. Create a new preview.")
            if dst.exists():
                raise ValueError(f"Destination already exists: {row['destination']}")
            checked.append((row, src, dst))
        staged, published, items = [], [], []
        lib = library(settings)
        try:
            for row, src, dst in checked:
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Temporary and final files share a filesystem. Hard-link publication
                # below is exclusive: an existing destination can never be overwritten.
                fd, name = tempfile.mkstemp(prefix=".mmm-", suffix=".tmp", dir=dst.parent)
                os.close(fd)
                temp = Path(name)
                staged.append(temp)
                shutil.copy2(src, temp)
                item = Item(lib, **dict(Item.from_path(str(src))))
                item.update(row["metadata"])
                item.write(path=str(temp))
                item.path = os.fsencode(dst)
                item.mtime = temp.stat().st_mtime
                stat = src.stat()
                item[PROVENANCE] = json.dumps({'source': row['source'], 'size': stat.st_size,
                                               'mtime_ns': stat.st_mtime_ns, 'sha256': row['fingerprint']['sha256']})
                items.append(item)
            for row, src, _ in checked:
                if fingerprint(src) != row['fingerprint']:
                    raise ValueError('Source file changed during import. Create a new preview.')
            for temp, (_, _, dst) in zip(staged, checked):
                os.link(temp, dst)
                published.append(dst)
            with lib.transaction():
                lib.add_album(items)
        except Exception:
            for dst in published:
                dst.unlink(missing_ok=True)
            raise
        finally:
            for temp in staged:
                temp.unlink(missing_ok=True)
        cleanup = {'status': 'kept', 'source': plan['source']}
        if plan.get('delete_source'):
            # Publishing and catalog commit have succeeded. Cleanup must never
            # turn a successful import into a failed job or roll back good copies.
            try:
                if snapshot(settings, plan['source'], audio_only=False)[0] != plan.get('inbox_snapshot'):
                    cleanup = {**cleanup, 'status': 'skipped', 'message': 'Inbox folder changed after preview; originals were kept.'}
                else:
                    folder = safe_path(settings.inbox, plan['source'], allow_root=False)
                    shutil.rmtree(folder)
                    cleanup['status'] = 'deleted'
            except Exception:
                cleanup = {**cleanup, 'status': 'failed', 'message': 'Import succeeded, but Inbox cleanup could not finish. Check the source folder and permissions.'}
        return {"album": plan["album"], "artist": plan["artist"], "files": len(items),
                "destinations": [row["destination"] for row in plan["rows"]], "cleanup": cleanup}


def update_inbox_paths(settings, source, destination):
    """Keep import provenance attached to Inbox sources moved through the UI."""
    if not (settings.state / 'beets.db').exists():
        return
    lib = library(settings)
    with lib.transaction():
        for item in lib.items():
            if not item.get(PROVENANCE):
                continue
            data = json.loads(item[PROVENANCE])
            path = data['source']
            if path == source or path.startswith(source + '/'):
                if destination is None:
                    del item[PROVENANCE]
                else:
                    data['source'] = destination + path[len(source):]
                    item[PROVENANCE] = json.dumps(data)
                item.store()


def update_catalog_paths(settings, source, destination):
    if not (settings.state / "beets.db").exists():
        return
    lib = library(settings)
    with lib.transaction():
        for item in lib.items():
            old = Path(os.fsdecode(item.path))
            if old == source or old.is_relative_to(source):
                item.path = os.fsencode(destination / old.relative_to(source))
                item.store()
