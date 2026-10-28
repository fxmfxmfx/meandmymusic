"""Conservative HTTP helpers for the optional local music agent."""
from __future__ import annotations

import re
import time
import unicodedata
import math
from pathlib import PurePosixPath
from urllib.parse import quote
from uuid import UUID

import httpx


class AgentError(RuntimeError):
    """An actionable, sanitized workflow failure."""


class SearchUnavailable(AgentError):
    """A completed Soulseek search that received no peer replies."""

    def __init__(self, message, *, reason="no_replies", details=None):
        super().__init__(message)
        self.reason = reason
        self.details = details or []


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    tokens, token = [], []
    for char in value:
        if char.isalnum(): token.append(char)
        elif token:
            tokens.append("".join(token)); token = []
    if token: tokens.append("".join(token))
    return " ".join(tokens)


class MusicAPI:
    def __init__(self, base_url: str, password: str, transport=None):
        self.client = httpx.Client(base_url=base_url.rstrip("/"), trust_env=False,
                                   transport=transport, timeout=150)
        try:
            self._request("POST", "/api/login", json={"password": password})
        except Exception:
            self.client.close()
            raise

    def close(self):
        self.client.close()

    def _request(self, method, path, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["X-Requested-With"] = "meandmymusic"
        try:
            # Only read operations may be retried. Queue/apply outcomes must be
            # journalled and reconciled, never repeated after a lost response.
            repeatable = method == "GET" or (method == "POST" and path == "/api/musicbrainz/match")
            for attempt in range(3):
                try:
                    response = self.client.request(method, path, headers=headers, **kwargs)
                except httpx.TransportError:
                    if not repeatable or attempt == 2:
                        raise
                    time.sleep(2 ** (attempt + 1))
                    continue
                if not repeatable or response.status_code not in (502, 503, 504) or attempt == 2:
                    break
                time.sleep(2 ** (attempt + 1))
            response.raise_for_status()
            if not response.content:
                return {}
            return response.json()
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            if code in (401, 403):
                message = "Manager authentication failed. Check the private API configuration."
            elif code == 502 and path.startswith('/api/musicbrainz/'):
                message = "MusicBrainz is unavailable through the manager. Check the manager connection and outbound proxy."
            else:
                message = f"Manager request failed (HTTP {code}). Check the corresponding task or operation in the manager."
            raise AgentError(message) from None
        except (httpx.HTTPError, ValueError):
            raise AgentError("The manager did not return a valid response. Check connectivity and retry.") from None

    def get(self, path, **kwargs): return self._request("GET", path, **kwargs)
    def post(self, path, **kwargs): return self._request("POST", path, **kwargs)
    def call(self, method, path, data=None, params=None):
        kwargs = {}
        if data is not None: kwargs["json"] = data
        if params is not None: kwargs["params"] = params
        return self._request(method, path if path.startswith("/api/") else "/api/" + path.lstrip("/"), **kwargs)


def _release_artist(r):
    return r.get("artist") or r.get("artist-credit") or r.get("artist_credit") or ""


def _release_title(r): return r.get("title") or r.get("album") or ""
def _track_count(r):
    value = r.get("track_count", r.get("trackcount", r.get("tracks", 0)))
    return value if type(value) is int else 0


def release_candidates(api, artist, album):
    def lucene(value): return str(value).replace("\\", "\\\\").replace('"', '\\"')
    query = f'artist:"{lucene(artist)}"'
    if album.strip(): query += f' AND release:"{lucene(album)}"'
    result = api.get("/api/musicbrainz/releases", params={"query": query})
    if not isinstance(result, list): return []
    def valid_id(value):
        try: UUID(str(value)); return True
        except (ValueError, TypeError, AttributeError): return False
    return [r for r in result if isinstance(r, dict) and valid_id(r.get("id")) and
            normalize(_release_artist(r)) == normalize(artist) and
            (not album.strip() or normalize(_release_title(r)) == normalize(album)) and
            0 < _track_count(r) <= 300]


def _safe_remote(path):
    raw = str(path).replace("\\", "/")
    p = PurePosixPath(raw)
    return "\x00" not in raw and not p.is_absolute() and all(x not in ("", ".", "..") for x in raw.split("/"))


def _get_search(api, query, wait, progress, *, mode="standard"):
    started = api.post("/api/soulseek/searches", json={"query": query, "mode": mode})
    sid = started.get("id") if isinstance(started, dict) else None
    if not sid: raise AgentError("Manager did not start the Soulseek search. Check the Soulseek connection and retry.")
    deadline = time.monotonic() + wait
    previous = None
    while time.monotonic() < deadline:
        snapshot = api.get(f"/api/soulseek/searches/{quote(str(sid), safe='')}")
        status, responses = snapshot.get("search", {}), snapshot.get("responses")
        update = (status.get("state", status.get("status", "searching")), status.get("fileCount"), status.get("responseCount"))
        if progress and update != previous:
            progress(f"{update[0]}: {update[1] or 0} files, {update[2] or 0} replies")
            previous = update
        flags = {part.strip().casefold() for k in ("state", "status") for part in str(status.get(k, "")).split(",")}
        terminal = status.get("isComplete") is True or bool(flags & {"completed", "cancelled", "timedout", "errored"})
        if terminal:
            if flags & {"cancelled", "errored"}:
                raise AgentError("Soulseek search was cancelled or failed before results were available.")
            if not isinstance(responses, list):
                # Fetch terminal snapshot once more; manager validates delivered counters.
                snapshot = api.get(f"/api/soulseek/searches/{quote(str(sid), safe='')}")
                responses = snapshot.get("responses")
            if not isinstance(responses, list) or not all(type(status.get(k)) is int for k in ("responseCount", "fileCount", "lockedFileCount")):
                time.sleep(min(0.2, max(0, deadline-time.monotonic())))
                continue
            if (not all(isinstance(x, dict) for x in responses) or
                len(responses) != status["responseCount"] or
                sum(len(x.get("files", [])) for x in responses if isinstance(x.get("files", []), list)) != status["fileCount"] or
                any(not isinstance(x.get("files", []), list) or not isinstance(x.get("lockedFiles", []), list) for x in responses) or
                sum(len(x.get("lockedFiles", [])) for x in responses if isinstance(x.get("lockedFiles", []), list)) != status["lockedFileCount"]):
                time.sleep(min(0.2, max(0, deadline-time.monotonic())))
                continue
            return responses if isinstance(responses, list) else []
        time.sleep(min(1, max(0, deadline-time.monotonic())))
    raise AgentError("Soulseek search timed out before results were complete.")


def search_sources(api, release, *, wait=70, progress=None):
    artist, album = _release_artist(release), _release_title(release)
    combined = f"{artist} {album}".strip()
    attempts = [(combined, "standard")]
    ascii_query = unicodedata.normalize("NFKD", combined).encode("ascii", "ignore").decode().strip()
    if ascii_query and ascii_query != combined and normalize(ascii_query) == normalize(combined):
        attempts.append((ascii_query, "standard"))
    groups = {}
    diagnostics = []

    def collect(query, mode):
        responses = _get_search(api, query, wait, progress, mode=mode)
        file_count = 0
        for peer in responses:
            peer_files = peer.get("files", [])
            if isinstance(peer_files, list): file_count += len(peer_files)
        diagnostics.append({"query": query, "mode": mode, "replies": len(responses), "files": file_count, "terminal": "completed"})
        for peer in responses:
            username = peer.get("username")
            if not isinstance(username, str) or not username: continue
            for file in peer.get("files", []) if isinstance(peer.get("files", []), list) else []:
                if not isinstance(file, dict): continue
                path, size = file.get("filename"), file.get("size")
                if not isinstance(path, str) or not _safe_remote(path): continue
                normalized_path = path.replace("\\", "/")
                remote = PurePosixPath(normalized_path)
                if remote.suffix.lower() != ".flac": continue
                if type(size) is not int or size <= 0: continue
                parts = remote.parts
                parent_parts = list(parts[:-1])
                if parent_parts and re.match(r"^(cd|disc)\s*0*\d+$", parent_parts[-1], re.I): parent_parts.pop()
                directory = "/".join(parent_parts)
                if not directory or not (normalize(artist) in normalize(directory) and normalize(album) in normalize(directory)): continue
                key = (username, directory)
                group = groups.setdefault(key, {"username": username, "directory": directory, "files": {}, "free_slots": peer.get("hasFreeUploadSlot"), "speed": peer.get("uploadSpeed"), "queue": peer.get("queueLength")})
                if path in group["files"] and group["files"][path] != size: group["files"].pop(path, None); group["conflict"] = True
                elif not group.get("conflict"): group["files"][path] = size

    for query, mode in attempts:
        collect(query, mode)
    output = build_sources(groups, release)
    # Broaden the query when the combined terms produce no complete source.
    # All original album/path/count/size checks remain in force while collecting.
    if not output and album.strip():
        album_query = album.strip()
        if all(normalize(album_query) != normalize(q) for q, _ in attempts):
            collect(album_query, "extended")
            attempts.append((album_query, "extended"))
            output = build_sources(groups, release)
    if not any(item["replies"] for item in diagnostics):
        raise SearchUnavailable("No Soulseek users replied to the completed searches.", details=diagnostics)
    return sort_sources(output)


def build_sources(groups, release):
    artist, album = _release_artist(release), _release_title(release)
    output = []
    for group in groups.values():
        files = group["files"]
        if group.get("conflict") or len(files) != _track_count(release): continue
        total = sum(files.values())
        if total > 8 * 1024**3: continue
        output.append({"username": group["username"], "directory": group["directory"], "files": [{"filename": p, "size": s} for p,s in sorted(files.items())], "total_bytes": total, "free_slots": group["free_slots"], "speed": group["speed"], "queue": group["queue"]})
    return output


def sort_sources(output):
    def number(value):
        return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0
    return sorted(output, key=lambda s: (s['free_slots'] is not True, number(s['queue']), -number(s['speed'])))


def queue_source(api, source):
    return api.post("/api/soulseek/downloads", json={"username": source["username"], "files": source["files"]})


def transfer_status(api, source):
    data = api.get("/api/soulseek/downloads")
    rows = {}
    for peer in data if isinstance(data, list) else []:
        if peer.get("username") != source["username"]: continue
        for transfer in peer.get("directories", []):
            for file in transfer.get("files", []): rows[(file.get("filename"), file.get("size"))] = file
    selected = source["files"]
    matches = [rows.get((f["filename"], f["size"])) for f in selected]
    states = [{part.strip().casefold() for part in str((x or {}).get("state", (x or {}).get("status", ""))).split(",")} for x in matches]
    nbytes = sum((x or {}).get("bytesTransferred", 0) for x in matches if type((x or {}).get("bytesTransferred")) is int)
    if any(x & {"failed", "cancelled", "canceled", "errored", "rejected", "timedout", "aborted"} for x in states): state = "failed"
    elif all(x and {"completed", "succeeded"} <= flags and x.get("bytesTransferred") == f["size"] for x,flags,f in zip(matches, states, selected)): state = "complete"
    else: state = "waiting"
    missing = [f["filename"] for f,x in zip(selected,matches) if x is None]
    return {"status": state, "files": sum(x is not None and {"completed", "succeeded"} <= flags and x.get("bytesTransferred") == f["size"] for x,flags,f in zip(matches,states,selected)), "found": sum(x is not None for x in matches), "bytes": nbytes, "total_bytes": sum(f["size"] for f in selected), "missing": missing}


def discover_inbox(api, source):
    base = PurePosixPath(source["directory"].replace("\\", "/"))
    expected = {}
    for f in source["files"]:
        remote = PurePosixPath(f["filename"].replace("\\", "/"))
        try: rel = remote.relative_to(base).as_posix()
        except ValueError: raise AgentError("Selected source paths do not share the source directory.") from None
        expected[rel] = f["size"]
    if len(expected) != len(source["files"]): raise AgentError("Selected source contains duplicate paths.")
    entries, todo, seen, visited = [], [""], 0, set()
    while todo:
        path = todo.pop(0)
        if path in visited: continue
        visited.add(path)
        listing = api.get("/api/files", params={"root":"inbox", "path":path})
        seen += 1
        if seen > 200: raise AgentError("Inbox scan exceeded 200 folders; narrow the Inbox first.")
        for entry in listing.get("entries", []):
            p = entry.get("path")
            if not isinstance(p, str) or not _safe_remote(p): continue
            if entry.get("directory"): todo.append(p)
            else: entries.append(entry)
    candidates = {}
    for entry in entries:
        p = PurePosixPath(entry["path"])
        for rel, size in expected.items():
            if p.as_posix().endswith("/" + rel) and entry.get("size") == size and p.suffix.lower() == ".flac":
                root = p.as_posix()[:-(len(rel)+1)]
                candidates.setdefault(root, {})[rel] = entry
    valid = []
    for root, found in candidates.items():
        if set(found) != set(expected): continue
        # Require every audio file under the candidate folder to belong to this selection.
        audio = [e for e in entries if e["path"].startswith(root + "/") and e.get("audio")]
        actual = {PurePosixPath(e["path"]).relative_to(PurePosixPath(root)).as_posix(): e.get("size") for e in audio}
        if actual == expected: valid.append(root)
    if len(valid) != 1: raise AgentError("Downloaded files are missing, extra or ambiguous in Inbox.")
    return valid[0]


def get_match(api, path, release):
    result = api.post("/api/musicbrainz/match", json={"path":path, "artist":_release_artist(release), "album":_release_title(release)})
    candidate = next((x for x in result.get("candidates", []) if x.get("id") == result.get("recommended_release_id")), {})
    if result.get("recommended_release_id") != release.get("id"): raise AgentError("Manager recommended a different release. Review the match manually.")
    confidence = candidate.get("confidence")
    if normalize(candidate.get("artist")) != normalize(_release_artist(release)) or normalize(candidate.get("title")) != normalize(_release_title(release)): raise AgentError("The matched artist or album does not match the selection.")
    if candidate.get("tracks") != _track_count(release) or type(candidate.get("missing_tracks")) is not int or candidate["missing_tracks"] != 0 or type(candidate.get("extra_files")) is not int or candidate["extra_files"] != 0 or not _finite_confidence(confidence): raise AgentError("Track match is incomplete or below 95% confidence.")
    return result


def create_preview(api, path, release):
    return api.post("/api/imports/preview", json={"path":path, "release_id":release["id"], "keep_source":True})


def _finite_confidence(value):
    return (type(value) in (int, float) and math.isfinite(value) and 95 <= value <= 100)


def inspect_preview(job, source, path, release):
    result = job.get("result", {})
    rows = result.get("rows", [])
    base = PurePosixPath(source["directory"].replace("\\", "/"))
    expected = {}
    for file in source["files"]:
        try: expected[PurePosixPath(file["filename"].replace("\\", "/")).relative_to(base).as_posix()] = file["size"]
        except ValueError: raise AgentError("Selected source paths do not share the source directory.") from None
    if len(expected) != len(source["files"]): raise AgentError("Selected source contains duplicate paths.")
    if job.get("status") != "ready" or result.get("delete_source") is not False or result.get("mode") != "copy": raise AgentError("Preview is not a safe ready copy. Review it manually.")
    if result.get("source") != path: raise AgentError("Preview source does not match the selected Inbox folder.")
    if len(rows) != len(expected): raise AgentError("Preview file list does not match the selected download.")
    actual = {}
    for row in rows:
        rowpath = row.get("source", "")
        if not isinstance(rowpath, str) or not _safe_remote(rowpath): raise AgentError("Preview contains an unsafe source path.")
        try: rel = PurePosixPath(rowpath).relative_to(PurePosixPath(path)).as_posix()
        except ValueError: raise AgentError("Preview source file is outside the selected Inbox folder.") from None
        fingerprint = row.get("fingerprint", {})
        if type(fingerprint.get("size")) is not int or fingerprint["size"] != expected.get(rel): raise AgentError("Preview file size does not match the selected download.")
        destination = row.get("destination", "")
        if not isinstance(destination, str) or not _safe_remote(destination): raise AgentError("Preview contains an unsafe Library destination.")
        actual[rel] = fingerprint["size"]
    if actual != expected: raise AgentError("Preview file list does not match the selected download.")
    created = result.get("created")
    if type(created) not in (int, float) or not math.isfinite(created) or time.time() - created > 3600 or created > time.time() + 60: raise AgentError("Preview is expired or has an invalid creation time.")
    if result.get("release_id") != release.get("id") or normalize(result.get("artist")) != normalize(_release_artist(release)) or normalize(result.get("album")) != normalize(_release_title(release)) or not _finite_confidence(result.get("confidence")): raise AgentError("Preview identifies a different or uncertain album.")
    if any(normalize(row.get("after", {}).get("albumartist") or row.get("after", {}).get("artist")) != normalize(_release_artist(release)) or
           normalize(row.get("after", {}).get("album")) != normalize(_release_title(release)) or
           row.get("after", {}).get("mb_albumid") != release.get("id") for row in rows):
        raise AgentError("Preview track metadata does not identify the selected release.")
    return job


def wait_job(api, job_id, timeout=600, progress=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = api.get(f"/api/jobs/{quote(str(job_id), safe='')}")
        if progress: progress(str(job.get("status", "waiting")))
        status = job.get("status")
        if status in ("ready", "done"): return job
        if status in ("failed", "cancelled", "canceled", "expired", "interrupted", "applied", "applying"): raise AgentError("Manager task is no longer available for review. Check its status in the manager.")
        if status not in ("pending", "queued", "running", "started", "waiting"): raise AgentError("Manager task entered an unexpected state. Check its status in the manager.")
        time.sleep(min(1, max(0, deadline-time.monotonic())))
    raise AgentError("Manager task timed out. Check its status in the manager.")
