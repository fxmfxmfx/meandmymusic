"""Run inside the manager container; prints access checks, never secret values."""
import os
from pathlib import Path
import socket
import stat
import tempfile


def main():
    print(f"Container identity: uid={os.getuid()} gid={os.getgid()} groups={os.getgroups()}")
    failures = 0
    for name, default in [("MUSIC_DIR", "/music"), ("INBOX_DIR", "/inbox"), ("STATE_DIR", "/state")]:
        path = Path(os.getenv(name, default))
        try:
            info = path.stat()
            print(f"{name}: {path}, owner={info.st_uid}:{info.st_gid}, mode={stat.filemode(info.st_mode)}")
            with tempfile.TemporaryFile(dir=path, prefix=".mmm-check-"):
                pass
            print("  write check: OK (temporary check file removed)")
        except OSError as error:
            failures += 1
            print(f"  write check: FAILED ({error.strerror or type(error).__name__}). Check PUID/PGID and host bind-mount ownership.")
    try:
        left, right = socket.socketpair()
        left.close()
        right.close()
        print("Unix socketpair: OK")
    except OSError as error:
        failures += 1
        print(f"Unix socketpair: FAILED ({error.strerror}). Check host AppArmor audit; this is unrelated to disk permissions.")
    key = os.getenv("SLSKD_API_KEY", "")
    valid = 16 <= len(key) <= 255 and ";" not in key
    print(f"Manager API key length: {'OK' if valid else 'INVALID'} (value hidden)")
    if not valid:
        failures += 1
    print("No passwords or account names were printed.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
