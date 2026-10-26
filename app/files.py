import os
import ctypes
import errno
import threading
import shutil
from pathlib import Path

from fastapi import HTTPException
from mutagen import File as AudioFile

AUDIO = {".flac", ".mp3", ".m4a", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".ape", ".wv"}
operation_lock = threading.RLock()


def rename_noreplace(source, destination):
    """Linux exclusive rename: protect even against a concurrent external writer."""
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "renameat2"):
        raise OSError(errno.ENOTSUP, "Moving files requires Linux with renameat2 support")
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def safe_path(root: Path, relative: str, *, exists=True, allow_root=True) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or "\x00" in relative or "\\" in relative:
        raise HTTPException(400, "Invalid path")
    current = root
    for part in raw.parts:
        current = current / part
        if current.is_symlink():
            raise HTTPException(400, "Symbolic links are not supported")
    target = current.resolve()
    if not target.is_relative_to(root):
        raise HTTPException(400, "Path is outside the music folder")
    if not allow_root and target == root:
        raise HTTPException(400, "The root folder cannot be modified")
    if exists and not target.exists():
        raise HTTPException(404, "File or folder not found")
    return target


def audio_metadata(path: Path):
    try:
        audio = AudioFile(path, easy=True)
        if audio is None:
            return {}
        def tag(key):
            values = audio.get(key, [])
            return str(values[0]) if values else ""
        return {"title": tag("title"), "artist": tag("artist"), "album": tag("album"),
                "albumartist": tag("albumartist"), "date": tag("date"), "track": tag("tracknumber"),
                "duration": round(audio.info.length), "bitrate": round(getattr(audio.info, "bitrate", 0) / 1000),
                "sample_rate": getattr(audio.info, "sample_rate", 0)}
    except Exception:
        return {}


def list_directory(root: Path, relative: str):
    directory = safe_path(root, relative)
    if not directory.is_dir():
        raise HTTPException(400, "Select a folder")
    entries = []
    for path in directory.iterdir():
        if path.is_symlink() or path.name.startswith("."):
            continue
        try:
            stat = path.stat()
            entries.append({"name": path.name, "path": path.relative_to(root).as_posix(),
                            "directory": path.is_dir(), "size": stat.st_size if path.is_file() else None,
                            "modified": stat.st_mtime, "audio": path.suffix.lower() in AUDIO})
        except FileNotFoundError:
            continue
    entries.sort(key=lambda x: (not x["directory"], x["name"].casefold()))
    return {"path": directory.relative_to(root).as_posix(), "entries": entries}


def move(root: Path, source: str, destination: str):
    with operation_lock:
        src = safe_path(root, source, allow_root=False)
        dst = safe_path(root, destination, exists=False, allow_root=False)
        if dst.exists():
            raise HTTPException(409, "A file or folder with this name already exists")
        if not dst.parent.is_dir():
            raise HTTPException(400, "Destination folder does not exist")
        if src.is_dir() and dst.is_relative_to(src):
            raise HTTPException(400, "A folder cannot be moved inside itself")
        try:
            rename_noreplace(src, dst)
        except FileExistsError:
            raise HTTPException(409, "Destination already exists. No files were overwritten.")
        except OSError as error:
            raise HTTPException(400, f"Move failed: {error.strerror}") from error
        return {"source": source, "destination": destination}


def delete(root: Path, relative: str):
    with operation_lock:
        target = safe_path(root, relative, allow_root=False)
        try:
            if target.is_dir():
                # rmtree unlinks nested symlinks; it never follows them outside.
                shutil.rmtree(target)
            else:
                target.unlink()
        except PermissionError:
            raise HTTPException(403, "Permission denied: cannot delete this item") from None
        except OSError:
            raise HTTPException(409, "Could not completely delete this item. Refresh the folder and check permissions.") from None
        return {"path": relative}
