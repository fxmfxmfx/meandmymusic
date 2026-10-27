"""Stream uploads to hidden staging, then publish a reviewed manifest exclusively."""
import os
import json
import shutil
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import anyio
from fastapi import HTTPException

from app.files import operation_lock, safe_path


class Uploads:
    def __init__(self, settings):
        self.settings = settings
        self.sessions = {}
        self.journal = settings.state / 'uploads.json'
        if self.journal.exists():
            for entry in json.loads(self.journal.read_text()):
                root = settings.inbox if entry['root'] == 'inbox' else settings.library
                name = entry['stage']
                if name.startswith('.mmm-upload-') and Path(name).name == name:
                    stale = root / name
                    if not stale.is_symlink():
                        shutil.rmtree(stale, ignore_errors=True)
        self._save_journal()

    def _save_journal(self):
        fd, name = tempfile.mkstemp(prefix='.uploads-', dir=self.settings.state)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump([{'root': value['root_name'], 'stage': value['stage'].name} for value in self.sessions.values()], file)
            os.replace(name, self.journal)
        finally:
            Path(name).unlink(missing_ok=True)

    def _session(self, upload_id):
        session = self.sessions.get(upload_id)
        if not session:
            raise HTTPException(404, 'Upload session not found. Start the upload again.')
        session['touched'] = time.monotonic()
        return session

    def _relative(self, value):
        path = Path(value)
        if len(value) > 4096 or not value or path.is_absolute() or '..' in path.parts or '\\' in value or '\x00' in value or path.as_posix() != value or value == '.':
            raise HTTPException(400, 'Upload paths must be relative file or folder names')
        return path

    def _target(self, session, relative):
        return safe_path(session['root'], (Path(session['path']) / self._relative(relative)).as_posix(), exists=False, allow_root=False)

    def begin(self, root_name, path, files, directories):
        with operation_lock:
            # Abandoned sessions expire; active requests refresh their timestamp.
            now = time.monotonic()
            for key, value in list(self.sessions.items()):
                if now - value['touched'] > 3600:
                    self.discard(key)
            if len(self.sessions) >= 4:
                raise HTTPException(409, 'Too many uploads in progress. Finish or cancel one first.')
            root = self.settings.inbox if root_name == 'inbox' else self.settings.library
            base = safe_path(root, path)
            if not base.is_dir():
                raise HTTPException(400, 'Select a destination folder')
            session = {'root': root, 'path': path, 'root_name': root_name, 'files': files,
                       'directories': directories, 'received': set(), 'writing': set(), 'touched': now}
            self._validate(session)
            if sum(file['size'] for file in files) > shutil.disk_usage(root).free:
                raise HTTPException(507, 'Not enough free space for this upload')
            stage = Path(tempfile.mkdtemp(prefix='.mmm-upload-', dir=root))
            session['stage'] = stage
            key = str(uuid4())
            self.sessions[key] = session
            self._save_journal()
            return {'id': key, 'files': len(files), 'bytes': sum(f['size'] for f in files)}

    def _validate(self, session):
        names = set()
        for file in session['files']:
            self._relative(file['path'])
            if file['path'] in names:
                raise HTTPException(409, 'Upload contains duplicate file paths')
            names.add(file['path'])
            target = self._target(session, file['path'])
            if target.exists():
                raise HTTPException(409, f'Already exists: {file["path"]}. No files were overwritten.')
            for parent in target.parents:
                if parent == session['root']:
                    break
                if parent.exists() and not parent.is_dir():
                    raise HTTPException(409, 'An upload folder conflicts with an existing file')
        for name in names:
            if any(parent.as_posix() in names for parent in Path(name).parents if parent.as_posix() != '.'):
                raise HTTPException(409, 'An upload file conflicts with a folder in the same upload')
        for directory in session['directories']:
            target = self._target(session, directory)
            if directory in names or any(parent.as_posix() in names for parent in Path(directory).parents if parent.as_posix() != '.'):
                raise HTTPException(409, 'An upload folder conflicts with a file in the same upload')
            if target.exists() and not target.is_dir():
                raise HTTPException(409, 'An upload folder conflicts with an existing file')

    async def receive(self, upload_id, index, request):
        session = self._session(upload_id)
        if index < 0 or index >= len(session['files']):
            raise HTTPException(404, 'Upload file not found')
        if index in session['writing'] or index in session['received']:
            raise HTTPException(409, 'This upload file is already being transferred')
        session['writing'].add(index)
        target = session['stage'] / str(index)
        expected, received = session['files'][index]['size'], 0
        try:
            async with await anyio.open_file(target, 'xb') as file:
                async for chunk in request.stream():
                    received += len(chunk)
                    session['touched'] = time.monotonic()
                    if received > expected:
                        raise HTTPException(413, 'Upload exceeds the declared file size')
                    await file.write(chunk)
            if received != expected:
                raise HTTPException(400, 'Upload was interrupted or the file size changed')
            if self.sessions.get(upload_id) is not session:
                raise HTTPException(409, 'Upload was cancelled')
            session['received'].add(index)
            return {'received': received}
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        finally:
            session['writing'].discard(index)

    def complete(self, upload_id):
        with operation_lock:
            session = self._session(upload_id)
            if session['writing'] or len(session['received']) != len(session['files']):
                raise HTTPException(409, 'Wait for every file to finish uploading')
            self._validate(session)
            published, created = [], []
            try:
                required = set()
                for file in session['files']:
                    required.update(self._target(session, file['path']).parents)
                for directory in session['directories']:
                    target = self._target(session, directory)
                    required.add(target)
                    required.update(target.parents)
                required = [p for p in required if p != session['root'] and p.is_relative_to(session['root'])]
                for directory in sorted(required, key=lambda p: len(p.parts)):
                    safe_path(session['root'], directory.relative_to(session['root']).as_posix(), exists=False)
                    try:
                        directory.mkdir()
                        created.append(directory)
                    except FileExistsError:
                        if not directory.is_dir():
                            raise HTTPException(409, 'Destination changed during upload')
                for index, file in enumerate(session['files']):
                    target = self._target(session, file['path'])
                    os.link(session['stage'] / str(index), target)
                    published.append(target)
            except Exception as error:
                for target in reversed(published):
                    target.unlink(missing_ok=True)
                for directory in reversed(created):
                    try:
                        directory.rmdir()
                    except OSError:
                        pass
                if isinstance(error, HTTPException):
                    raise
                raise HTTPException(409, 'Could not publish the upload. Check free space, permissions and destination conflicts. No existing files were overwritten.') from None
            folders = set(session['directories'])
            for file in session['files']:
                folders.update(parent.as_posix() for parent in Path(file['path']).parents if parent.as_posix() != '.')
            result = {'files': len(session['files']), 'folders': len(folders),
                      'root': session['root_name'], 'path': session['path']}
            self.discard(upload_id)
            return result

    def discard(self, upload_id):
        with operation_lock:
            session = self.sessions.pop(upload_id, None)
            if session:
                shutil.rmtree(session['stage'], ignore_errors=True)
                self._save_journal()

    def close(self):
        for key in list(self.sessions):
            self.discard(key)
