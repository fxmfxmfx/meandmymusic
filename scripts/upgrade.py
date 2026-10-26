#!/usr/bin/env python3
"""Remove legacy plaintext Soulseek credentials without printing or backing them up."""
import argparse
import os
from pathlib import Path
import re
import tempfile


def upgrade(path, lan=False):
    lines = path.read_text().splitlines()
    removed, output = 0, []
    for line in lines:
        if re.match(r"^\s*(?:export\s+)?(?:SOULSEEK_USERNAME|SOULSEEK_PASSWORD)\s*=", line):
            removed += 1
            continue
        if lan and re.match(r"^\s*APP_BIND\s*=", line):
            line = "APP_BIND=0.0.0.0"
        output.append(line)
    if lan and not any(re.match(r"^\s*APP_BIND\s*=", line) for line in output):
        output.append("APP_BIND=0.0.0.0")
    fd, name = tempfile.mkstemp(prefix=".env-upgrade-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            file.write("\n".join(output) + "\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return removed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lan", action="store_true", help="Publish the password-protected UI on host interfaces (APP_BIND=0.0.0.0)")
    args = parser.parse_args()
    path = Path(__file__).resolve().parents[1] / ".env"
    if not path.is_file():
        raise SystemExit("No .env found. Run python3 scripts/setup.py first.")
    count = upgrade(path, args.lan)
    print(f"Removed {count} legacy Soulseek credential entries. .env permissions: 0600.")
    print("Enter Soulseek credentials again in the web UI; APP_PASSWORD and API key were preserved.")
