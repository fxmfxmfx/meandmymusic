"""Beets matches and writes metadata; imports always copy and keep the inbox intact."""
import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path

from beets import autotag
from beets.library import Item, Library

from app.files import AUDIO, operation_lock, safe_path
from app.integrations import musicbrainz_lock

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


def preview(settings, source, release_id):
    folder = safe_path(settings.inbox, source, allow_root=False)
    if not folder.is_dir():
        raise ValueError("Для импорта выберите папку одного альбома во входящих")
    paths = sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO and p.is_file())
    if not paths or len(paths) > 300:
        raise ValueError("Выберите папку альбома с 1–300 аудиофайлами")
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
            _, _, proposal = autotag.tag_album(items, search_ids=[release_id])
        finally:
            time.sleep(1.1)
    if not proposal.candidates:
        raise ValueError("beets не смог сопоставить файлы с выбранным релизом")
    match = proposal.candidates[0]
    if match.extra_items or match.extra_tracks:
        raise ValueError(f"Неполное совпадение: лишних файлов {len(match.extra_items)}, недостающих треков {len(match.extra_tracks)}. Выберите другое издание или дополните альбом.")
    autotag.apply_metadata(match.info, match.mapping)
    lib.add_album(items)
    rows, targets = [], set()
    for item in items:
        path = Path(os.fsdecode(item.path))
        destination = Path(os.fsdecode(item.destination()))
        relative = destination.relative_to(settings.library).as_posix()
        safe_path(settings.library, relative, exists=False, allow_root=False)
        if destination.exists() or relative in targets:
            raise ValueError(f"Файл назначения уже существует или дублируется: {relative}")
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
            raise ValueError("Предпросмотр старше часа. Создайте новый.")
        checked = []
        for row in plan["rows"]:
            src = safe_path(settings.inbox, row["source"], allow_root=False)
            dst = safe_path(settings.library, row["destination"], exists=False, allow_root=False)
            if fingerprint(src) != row["fingerprint"]:
                raise ValueError(f"Исходный файл изменился: {row['source']}. Создайте новый предпросмотр.")
            if dst.exists():
                raise ValueError(f"Файл назначения уже существует: {row['destination']}")
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
