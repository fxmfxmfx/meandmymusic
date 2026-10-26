#!/usr/bin/env python3
"""Explicit live check: creates ONE disposable public Soulseek account.

Run only with an isolated Compose stack and scripts/setup.py-generated .env.
Never runs on normal CI pushes, searches, downloads, or shares files.
Credentials exist only in this process and slskd's RAM configuration, and are
cleared from the stack on exit. The public account itself cannot be deleted.
"""
import json
import secrets
import socket
import sys
import time
from pathlib import Path

import httpx


def main():
    values = dict(line.split("=", 1) for line in Path(".env").read_text().splitlines()
                  if line and not line.startswith("#") and "=" in line)
    username = "mmmtest_" + secrets.token_hex(8)
    password = secrets.token_urlsafe(24)
    stage = "archive_login"
    try:
        with socket.create_connection(("vps.slsknet.org", 2271), timeout=8):
            print("Soulseek TCP 2271: reachable", flush=True)
    except OSError as error:
        print("Soulseek TCP 2271: " + type(error).__name__, flush=True)
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=150, trust_env=False,
                      headers={"X-Requested-With": "meandmymusic"}) as client:
        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.is_error:
                payload = response.json()
                failure = payload.get("error", {})
                print(json.dumps({"check": stage, "http_status": response.status_code,
                                  "code": failure.get("code"), "stage": failure.get("stage"),
                                  "message": failure.get("message")}, ensure_ascii=False), flush=True)
                # Explicit isolated CI diagnosis only; never exposed in the web UI.
                try:
                    with httpx.Client(base_url="http://127.0.0.1:5030/api/v0", trust_env=False,
                                      headers={"X-API-Key": values["SLSKD_API_KEY"]}, timeout=8) as slskd:
                        logs = slskd.get("/logs/live").json()
                        text = json.dumps(logs[-30:] if isinstance(logs, list) else {"unexpected_log_shape": type(logs).__name__})
                        for secret in (username, password, values["APP_PASSWORD"], values["SLSKD_API_KEY"]):
                            text = text.replace(secret, "[REDACTED]")
                        print("Isolated slskd diagnostic: " + text, flush=True)
                except Exception:
                    print("Isolated slskd diagnostic unavailable", flush=True)
                raise RuntimeError("Live Soulseek check failed; safe diagnostic printed above")
            return response.json()

        try:
            call("POST", "/api/login", json={"password": values["APP_PASSWORD"]})
            for action in ("register", "login"):
                stage = action
                data = {"username": username, "password": password, "remember": False}
                if action == "register":
                    data["password_confirm"] = password
                result = call("POST", "/api/soulseek/auth/" + action, json=data)
                status = call("GET", "/api/soulseek/auth/status")
                if not (result.get("logged_in") is True and status.get("logged_in") is True
                        and status.get("username") == username and status.get("saved") is False):
                    raise RuntimeError("Server did not confirm the expected account")
                print(action + ": server-confirmed login; credentials not saved", flush=True)
                stage = "logout"
                call("POST", "/api/soulseek/auth/logout")
                status = call("GET", "/api/soulseek/auth/status")
                if status.get("logged_in") or status.get("saved"):
                    raise RuntimeError("Logout was not confirmed")
                print("logout: confirmed", flush=True)
                time.sleep(3)
            print("LIVE SOULSEEK REGISTER + LOGIN: PASS", flush=True)
        finally:
            # Do not expose upstream bodies or credentials even if cleanup fails.
            try:
                response = client.post("/api/soulseek/auth/logout")
                print("cleanup HTTP " + str(response.status_code), flush=True)
            except httpx.HTTPError:
                print("cleanup: API unavailable; workflow tears down RAM config", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Live check failed (" + type(error).__name__ + "); credentials withheld.", flush=True)
        sys.exit(1)
