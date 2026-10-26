"""JSON-first CLI for humans and agents. Uses the same authenticated API as the UI."""
import argparse
import getpass
import json
import os
import sys
import time
from pathlib import Path

import httpx


def parser():
    p = argparse.ArgumentParser(prog="mmm", description="meandmymusic CLI — JSON output, shared server API")
    p.add_argument("--url", default=os.getenv("MMM_URL", "http://localhost:8080"))
    p.add_argument("--password-env", default="MMM_PASSWORD", help="Name of environment variable containing the server password")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    ls = sub.add_parser("ls")
    ls.add_argument("path", nargs="?", default="")
    ls.add_argument("--root", choices=["library", "inbox"], default="library")
    mkdir = sub.add_parser("mkdir")
    mkdir.add_argument("path")
    mkdir.add_argument("--root", choices=["library", "inbox"], default="library")
    mv = sub.add_parser("mv")
    mv.add_argument("source")
    mv.add_argument("destination")
    mv.add_argument("--root", choices=["library", "inbox"], default="library")
    search = sub.add_parser("search", help="Start a Soulseek search and collect results")
    search.add_argument("query")
    search.add_argument("--wait", type=int, default=20, help="Wait at most this many seconds (0–120)")
    results = sub.add_parser("results")
    results.add_argument("search_id")
    download = sub.add_parser("download", help="Queue selected files from an explicit JSON manifest")
    download.add_argument("--user", required=True)
    download.add_argument("--files", type=Path, required=True, help='JSON array of {"filename": "remote path", "size": 123}')
    sub.add_parser("downloads")
    releases = sub.add_parser("releases", help="Search MusicBrainz release editions")
    releases.add_argument("query")
    preview = sub.add_parser("preview", help="Match one inbox album to an exact release; does not modify music")
    preview.add_argument("path")
    preview.add_argument("--release", required=True, help="MusicBrainz release UUID")
    preview.add_argument("--wait", action="store_true")
    apply = sub.add_parser("apply", help="Apply a reviewed preview by ID (copies, tags and catalogs files)")
    apply.add_argument("plan_id")
    apply.add_argument("--wait", action="store_true")
    sub.add_parser("jobs")
    job = sub.add_parser("job")
    job.add_argument("job_id")
    job.add_argument("--wait", action="store_true")
    sub.add_parser("history")
    undo = sub.add_parser("undo")
    undo.add_argument("event_id")
    return p


def execute(args, client):
    def call(method, path, data=None, params=None):
        response = client.request(method, "/api" + path, json=data, params=params)
        result = response.json()
        if response.is_error:
            raise RuntimeError(result.get("detail", f"HTTP {response.status_code}"))
        return result

    def wait_for_job(job_id):
        deadline = time.monotonic() + 600
        while True:
            job = call("GET", f"/jobs/{job_id}")
            if job["status"] not in {"queued", "running"}:
                if job["status"] in {"failed", "interrupted"}:
                    raise RuntimeError(job.get("error") or job["status"])
                return job
            if time.monotonic() >= deadline:
                raise RuntimeError(f"Timed out waiting; the server job continues: {job_id}")
            time.sleep(2)

    password = os.getenv(args.password_env)
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Archive password: ")
    if not password:
        raise RuntimeError(f"Set {args.password_env} to the archive password; credentials are never saved by this CLI")
    call("POST", "/login", {"password": password})
    c = args.command
    if c == "status":
        return call("GET", "/system")
    if c == "ls":
        return call("GET", "/files", params={"root": args.root, "path": args.path})
    if c == "mkdir":
        return call("POST", "/folders", {"root": args.root, "path": args.path})
    if c == "mv":
        return call("POST", "/move", {"root": args.root, "source": args.source, "destination": args.destination})
    if c == "search":
        if not 0 <= args.wait <= 120:
            raise RuntimeError("--wait must be between 0 and 120 seconds")
        search = call("POST", "/soulseek/searches", {"query": args.query})
        deadline = time.monotonic() + args.wait
        result = {"search": search, "responses": []}
        while time.monotonic() < deadline:
            result = call("GET", f"/soulseek/searches/{search['id']}")
            if result["search"].get("isComplete"):
                break
            time.sleep(2)
        return result
    if c == "results":
        return call("GET", f"/soulseek/searches/{args.search_id}")
    if c == "download":
        files = json.loads(args.files.read_text())
        return call("POST", "/soulseek/downloads", {"username": args.user, "files": files})
    if c == "downloads":
        return call("GET", "/soulseek/downloads")
    if c == "releases":
        return call("GET", "/musicbrainz/releases", params={"query": args.query})
    if c == "preview":
        job = call("POST", "/imports/preview", {"path": args.path, "release_id": args.release})
        return wait_for_job(job["id"]) if args.wait else job
    if c == "apply":
        job = call("POST", f"/imports/{args.plan_id}/apply")
        return wait_for_job(job["id"]) if args.wait else job
    if c == "jobs":
        return call("GET", "/jobs")
    if c == "job":
        return wait_for_job(args.job_id) if args.wait else call("GET", f"/jobs/{args.job_id}")
    if c == "history":
        return call("GET", "/history")
    if c == "undo":
        return call("POST", f"/history/{args.event_id}/undo")


def main():
    args = parser().parse_args()
    if not args.url.startswith(("http://", "https://")):
        print(json.dumps({"error": "--url must use http:// or https://"}), file=sys.stderr)
        return 1
    try:
        with httpx.Client(base_url=args.url.rstrip("/"), timeout=40, follow_redirects=False, trust_env=False,
                          headers={"X-Requested-With": "meandmymusic"}) as client:
            result = execute(args, client)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (httpx.HTTPError, RuntimeError, ValueError, OSError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
