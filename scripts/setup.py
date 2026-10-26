#!/usr/bin/env python3
"""Bootstrap a new checkout. Never rewrites .env or chmods an existing archive."""
import getpass
import os
from pathlib import Path
import secrets

base = Path(__file__).resolve().parents[1]
target = base / ".env"
if target.exists():
    raise SystemExit(".env already exists; edit it directly. No changes were made.")
username = input("Soulseek username: ").strip()
password = getpass.getpass("Soulseek password: ")
if not username or not password or any(c in username + password for c in "\n\r'"):
    raise SystemExit("Username/password must not be empty or contain newlines or single quotes. For other values, configure .env manually.")
uid = int(os.environ.get("PUID", os.getuid()))
gid = int(os.environ.get("PGID", os.getgid()))
archive_password = secrets.token_urlsafe(24)
api_key = secrets.token_hex(32)
values = (base / ".env.example").read_text()
values = values.replace("replace-with-a-unique-password-at-least-12-characters", archive_password)
values = values.replace("replace-with-a-random-api-key-at-least-16-characters", api_key)
values = values.replace("your-soulseek-username", "'" + username + "'")
values = values.replace("your-soulseek-password", "'" + password + "'")
values = values.replace("PUID=1000", f"PUID={uid}").replace("PGID=1000", f"PGID={gid}")
for relative in ["data/music", "data/inbox", "data/state", "data/slskd", "data/slskd/incomplete"]:
    directory = base / relative
    if not directory.exists():
        directory.mkdir(parents=True, exist_ok=True)
        if os.getuid() == 0:
            os.chown(directory, uid, gid)
with target.open("x") as file:
    os.chmod(target, 0o600)
    file.write(values)
print("Created .env and data directories. Archive password is in .env (APP_PASSWORD).")
print("Start: docker compose up -d --build")
print("Open: http://localhost:8080")
