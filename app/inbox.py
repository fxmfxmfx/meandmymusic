"""Inbox snapshots, album discovery and durable processing badges."""
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

from fastapi import HTTPException

from app.files import AUDIO, safe_path

PROVENANCE = 'mmm_inbox_import'
DISC = re.compile(r'^[\[(]?(?:cd|disc|disk)[\s._-]*\d+', re.I)


def records(settings, relative, *, audio_only=True):
    target = safe_path(settings.inbox, relative)
    found = []
    def add(path):
        stat = path.lstat()
        if audio_only and (path.is_symlink() or not path.is_file() or path.suffix.lower() not in AUDIO):
            return
        found.append((path.relative_to(settings.inbox).as_posix(), stat.st_size, stat.st_mtime_ns,
                      'link' if path.is_symlink() else 'dir' if path.is_dir() else 'file'))
        if len(found) > 50000:
            raise HTTPException(400, 'Too many files. Open a smaller Inbox folder.')
    if target.is_file():
        add(target)
    else:
        def failed(error):
            raise HTTPException(403, 'Cannot read an Inbox folder. Check directory permissions.') from error
        for base, dirs, files in os.walk(target, followlinks=False, onerror=failed):
            for name in dirs + files:
                add(Path(base) / name)
    return sorted(found)


def signature(relative, rows):
    base = Path(relative)
    values = [(Path(path).relative_to(base).as_posix(), *data) for path, *data in rows]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def snapshot(settings, relative, *, audio_only=True):
    rows = records(settings, relative, audio_only=audio_only)
    return signature(relative, rows), rows


def album_paths(settings, relative):
    base = safe_path(settings.inbox, relative)
    if not base.is_dir():
        raise HTTPException(400, 'Select an Inbox folder')
    albums, visited = [], 0
    def visit(folder):
        nonlocal visited
        visited += 1
        if visited > 10000:
            raise HTTPException(400, 'Too many folders. Open a smaller Inbox folder.')
        entries = sorted((p for p in folder.iterdir() if not p.is_symlink() and not p.name.startswith('.')), key=lambda p: p.name.casefold())
        dirs = [p for p in entries if p.is_dir()]
        audio = any(p.is_file() and p.suffix.lower() in AUDIO for p in entries)
        # A disc-only album is one album, rather than a separate job per disc.
        disc_dirs = [p for p in dirs if DISC.match(p.name)]
        disc_album = bool(disc_dirs) and all(p in disc_dirs or not records(settings, p.relative_to(settings.inbox).as_posix()) for p in dirs)
        if folder != settings.inbox and (audio or disc_album):
            albums.append(folder.relative_to(settings.inbox).as_posix())
            if len(albums) > 200:
                raise HTTPException(400, 'Match all supports up to 200 albums. Open a smaller Inbox folder.')
        else:
            for child in dirs:
                visit(child)
    visit(base)
    return albums


def imported_sources(settings):
    catalog = settings.state / 'beets.db'
    if not catalog.exists():
        return {}
    # Read only this app's flex attributes, without loading every library item.
    with sqlite3.connect(catalog.as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute('SELECT i.path, a.value FROM item_attributes a JOIN items i ON i.id=a.entity_id WHERE a.key=?', (PROVENANCE,)).fetchall()
    sources = {}
    for destination, value in rows:
        try:
            record = json.loads(value)
            path = Path(os.fsdecode(destination)).relative_to(settings.library).as_posix()
            if safe_path(settings.library, path).is_file():
                sources[record['source']] = record
        except (ValueError, TypeError, KeyError, HTTPException):
            continue
    return sources


def annotate(settings, listing, store):
    imports, matches = imported_sources(settings), store.matches()
    memo = {}
    def scanned(path):
        if path not in memo:
            memo[path] = snapshot(settings, path)
        return memo[path]
    scope = '' if listing['path'] == '.' else listing['path']
    valid, matched = {}, set()
    for path, cache in matches.items():
        if scope and not (path == scope or path.startswith(scope + '/') or scope.startswith(path + '/')):
            continue
        try:
            digest, rows = scanned(path)
        except HTTPException:
            continue
        if digest == cache['signature']:
            valid[path] = cache['result']
            if cache['result'].get('selected_release_id') or cache['result'].get('recommended_release_id'):
                matched.update(row[0] for row in rows)
    for entry in listing['entries']:
        if not entry['directory'] and not entry['audio']:
            continue
        _, rows = scanned(entry['path'])
        imported = sum(1 for path, size, mtime, _ in rows if path in imports and
                       imports[path]['size'] == size and imports[path]['mtime_ns'] == mtime)
        matched_count = sum(1 for path, size, mtime, _ in rows if path in matched or (path in imports and imports[path].get('matched', True) and
                            imports[path]['size'] == size and imports[path]['mtime_ns'] == mtime))
        cache = valid.get(entry['path'])
        entry['processing'] = {'audio_files': len(rows), 'imported_files': imported,
                               'imported': bool(rows) and imported == len(rows),
                               'matched_files': matched_count,
                               'match': 'matched' if rows and matched_count == len(rows) else
                               'partial' if matched_count else 'review' if cache and cache.get('candidates') else
                               'none' if cache else 'unmatched'}
    return listing
