"""Read-only duration inventory, including music outside the beets catalog."""
import math
import os
import sqlite3
import stat
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from mediafile import MediaFile
from fastapi import HTTPException

from app.files import AUDIO, operation_lock, safe_path


class LibraryStats:
    def __init__(self, settings):
        self.settings = settings
        self.database = settings.state / "library-stats.db"
        self.lock = threading.Lock()
        self.stopping = threading.Event()
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="library-time")
        self.running = False
        self.checked = 0.0
        self.progress = 0
        self.error = None
        self.result = None
        with sqlite3.connect(self.database) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS tracks (
                path TEXT PRIMARY KEY, signature TEXT, artist TEXT, album TEXT,
                duration REAL, generation TEXT)""")

    def get(self, refresh=False):
        with self.lock:
            if not self.running and not self.stopping.is_set() and (
                refresh or time.monotonic() - self.checked >= 60 or not self.checked
            ):
                self.running, self.progress, self.error = True, 0, None
                self.worker.submit(self._scan)
            return {**(self.result or {"artists": [], "duration": 0, "tracks": 0,
                                      "albums": 0, "skipped": 0, "unreadable_folders": 0}),
                    "ready": self.result is not None, "scanning": self.running,
                    "scanned": self.progress, "error": self.error}

    def close(self):
        self.stopping.set()
        self.worker.shutdown(wait=True)

    def _scan(self):
        try:
            result = self._collect()
            with self.lock:
                if result is not None:
                    self.result = result
        except Exception:
            with self.lock:
                self.error = "Could not read Library time. Check access to Library and application state, then refresh."
        finally:
            with self.lock:
                self.running = False
                self.checked = time.monotonic()

    def _collect(self):
        artists = {}
        skipped = unreadable = scanned = 0
        generation = uuid4().hex

        def walk_error(_):
            nonlocal unreadable
            unreadable += 1

        with sqlite3.connect(self.database) as db:
            for directory, directories, files in os.walk(self.settings.library, followlinks=False, onerror=walk_error):
                directories[:] = sorted(name for name in directories if not name.startswith(".")
                                        and not (Path(directory) / name).is_symlink())
                for name in sorted(files):
                    if self.stopping.is_set():
                        return None
                    if name.startswith(".") or os.path.splitext(name)[1].lower() not in AUDIO:
                        continue
                    path = Path(directory) / name
                    relative = path.relative_to(self.settings.library).as_posix()
                    # Brief per-file locking leaves file browsing/operations available.
                    # safe_path excludes symlinks, and stat excludes devices/FIFOs.
                    try:
                        with operation_lock:
                            path = safe_path(self.settings.library, relative)
                            info = path.stat()
                            if not stat.S_ISREG(info.st_mode):
                                continue
                            signature = repr((info.st_dev, info.st_ino, info.st_size,
                                              info.st_mtime_ns, info.st_ctime_ns))
                            row = db.execute("SELECT signature, artist, album, duration FROM tracks WHERE path=?",
                                             (relative,)).fetchone()
                            if row and row[0] == signature:
                                artist, album, duration = row[1:]
                            else:
                                try:
                                    media = MediaFile(path)
                                    duration = float(media.length)
                                    if not math.isfinite(duration) or duration <= 0:
                                        raise ValueError("No duration")
                                    artist = (media.albumartist or media.artist or "").strip() or "Unknown artist"
                                    album = (media.album or "").strip() or "Unknown album"
                                except Exception:
                                    artist, album, duration = "", "", 0
                            db.execute("INSERT OR REPLACE INTO tracks VALUES (?, ?, ?, ?, ?, ?)",
                                       (relative, signature, artist, album, duration, generation))
                    except (OSError, HTTPException):
                        skipped += 1
                        continue
                    scanned += 1
                    with self.lock:
                        self.progress = scanned
                    if not duration:
                        skipped += 1
                        continue
                    albums = artists.setdefault(artist, {})
                    entry = albums.setdefault(album, {"name": album, "duration": 0, "tracks": 0})
                    entry["duration"] += duration
                    entry["tracks"] += 1
            db.execute("DELETE FROM tracks WHERE generation != ?", (generation,))

        result = []
        for name, albums in artists.items():
            albums = sorted(albums.values(), key=lambda entry: (-entry["duration"], entry["name"].casefold()))
            result.append({"name": name, "duration": sum(a["duration"] for a in albums),
                           "tracks": sum(a["tracks"] for a in albums), "albums": albums})
        result.sort(key=lambda entry: (-entry["duration"], entry["name"].casefold()))
        return {"artists": result, "duration": sum(a["duration"] for a in result),
                "tracks": sum(a["tracks"] for a in result), "albums": sum(len(a["albums"]) for a in result),
                "skipped": skipped, "unreadable_folders": unreadable, "updated": time.time()}
