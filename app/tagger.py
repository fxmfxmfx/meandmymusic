"""Beets matches and writes metadata; imports always copy and keep the inbox intact."""
import hashlib
import os
import shutil
import tempfile
import time
from collections import Counter
from pathlib import Path

from beets import autotag
from beets.library import Item, Library

from app.files import AUDIO, operation_lock, safe_path
from app.integrations import musicbrainz_lock
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
    with musicbrainz_lock:
        try:
            with beets_proxy(settings.outbound_proxy):
                _, _, proposal = autotag.tag_album(items, **kwargs)
        finally:
            time.sleep(1.1)
    candidates = []
    for match in sorted(proposal.candidates, key=lambda candidate: float(candidate.distance))[:12]:
        info = match.info
        candidates.append({"id": info.album_id, "artist": info.artist, "title": info.album,
                           "date": str(info.year or ""), "country": info.country or "",
                           "format": info.media or "", "tracks": len(info.tracks),
                           "disambiguation": info.albumdisambig or "",
                           "confidence": round(max(0, 1 - float(match.distance)) * 100, 1),
                           "extra_files": len(match.extra_items), "missing_tracks": len(match.extra_tracks)})
    return {"artist": search_artist, "album": search_album, "tracks": len(items),
            "has_tags": bool(tagged_artist or tagged_album), "candidates": candidates}


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
            "created": time.time(), "mode": "copy"}


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
                items.append(item)
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
        return {"album": plan["album"], "artist": plan["artist"], "files": len(items),
                "destinations": [row["destination"] for row in plan["rows"]]}


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
