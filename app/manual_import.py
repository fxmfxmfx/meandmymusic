"""Copy a complete Inbox folder to a chosen Library location without retagging."""
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from beets.library import Item

from app.files import AUDIO, operation_lock, rename_noreplace, safe_path
from app.inbox import PROVENANCE, snapshot
from app.tagger import catalog_transaction, cleanup_inbox, fingerprint, library


def preview_manual(settings, source, destination):
    folder = safe_path(settings.inbox, source, allow_root=False)
    target = safe_path(settings.library, destination, exists=False, allow_root=False)
    if not folder.is_dir() or not target.parent.is_dir():
        raise ValueError('Choose an Inbox folder and an existing destination folder in Library')
    if target.exists():
        raise ValueError('Destination already exists. Choose another album folder name.')
    digest, entries = snapshot(settings, source, audio_only=False)
    rows, directories = [], []
    for path, _, _, kind in entries:
        item = safe_path(settings.inbox, path, allow_root=False)
        relative = item.relative_to(folder)
        if kind == 'dir':
            directories.append(relative.as_posix())
        elif not item.is_file():
            raise ValueError('Only regular files and folders can be added to Library')
        else:
            rows.append({'source':path, 'destination':(target / relative).relative_to(settings.library).as_posix(),
                         'relative':relative.as_posix(), 'fingerprint':fingerprint(item), 'audio':item.suffix.lower() in AUDIO})
    if digest != snapshot(settings, source, audio_only=False)[0]:
        raise ValueError('Inbox folder changed while preparing the preview. Try again.')
    return {'mode':'manual', 'source':source, 'destination':destination, 'artist':'', 'album':folder.name,
            'rows':rows, 'directories':directories, 'created':time.time(), 'inbox_snapshot':digest}


def apply_manual(settings, plan):
    with operation_lock:
        if time.time() - plan['created'] > 3600:
            raise ValueError('This preview is over an hour old. Create a new one.')
        source = safe_path(settings.inbox, plan['source'], allow_root=False)
        target = safe_path(settings.library, plan['destination'], exists=False, allow_root=False)
        if target.exists():
            raise ValueError('Destination already exists. No files were overwritten.')
        if not target.parent.is_dir() or not source.is_dir():
            raise ValueError('Source or destination folder no longer exists')
        if snapshot(settings, plan['source'], audio_only=False)[0] != plan['inbox_snapshot']:
            raise ValueError('Inbox folder changed. Create a new preview.')
        stage = Path(tempfile.mkdtemp(prefix='.mmm-copy-', dir=target.parent))
        published = False
        lib = library(settings)
        try:
            items = []
            for directory in plan['directories']:
                (stage / directory).mkdir(parents=True, exist_ok=True)
            for row in plan['rows']:
                src = safe_path(settings.inbox, row['source'], allow_root=False)
                if fingerprint(src) != row['fingerprint']:
                    raise ValueError('Source file changed. Create a new preview.')
                temp = stage / row['relative']
                temp.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, temp)
                if row['audio']:
                    try:
                        item = Item.from_path(str(src))
                    except Exception:
                        # Copying as-is must also work for files beets cannot read.
                        item = Item(path=os.fsencode(src), title=src.stem, album=source.name)
                    item.path = os.fsencode(target / row['relative'])
                    item.mtime = temp.stat().st_mtime
                    stat = src.stat()
                    item[PROVENANCE] = json.dumps({'source':row['source'], 'size':stat.st_size,
                                                   'mtime_ns':stat.st_mtime_ns, 'sha256':row['fingerprint']['sha256'], 'matched':False})
                    items.append(item)
            for row in plan['rows']:
                if fingerprint(safe_path(settings.inbox, row['source'])) != row['fingerprint']:
                    raise ValueError('Source file changed during copying. Create a new preview.')
            if snapshot(settings, plan['source'], audio_only=False)[0] != plan['inbox_snapshot']:
                raise ValueError('Inbox folder changed during copying. Create a new preview.')
            with catalog_transaction(lib):
                if items:
                    lib.add_album(items)
                rename_noreplace(stage, target)
                published = True
        except Exception:
            if published:
                shutil.rmtree(target)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return {'album':plan['album'], 'artist':'', 'files':len(plan['rows']), 'manual':True,
                'destination':plan['destination'], 'destinations':[row['destination'] for row in plan['rows']],
                'cleanup':cleanup_inbox(settings, plan)}
