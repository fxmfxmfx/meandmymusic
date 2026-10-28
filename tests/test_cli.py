import json

import httpx
import pytest

from app.cli import execute, parser


def _client(handler):
    return httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(handler),
        headers={"X-Requested-With": "meandmymusic"},
    )


def _login_response(request):
    assert request.url.path == "/api/login"
    assert request.read() == b'{"password":"test-secret"}'
    return httpx.Response(200, json={"ok": True})


def test_search_default_wait_is_60():
    args = parser().parse_args(["search", "artist album"])
    assert args.wait == 60
    assert args.extended is False


def test_search_extended_sends_mode_and_wait_zero_only_starts(monkeypatch):
    monkeypatch.setenv("MMM_PASSWORD", "test-secret")
    calls = []

    def handler(request):
        if request.url.path == "/api/login":
            return _login_response(request)
        calls.append((request.method, request.url.path, json.loads(request.read()) if request.method == "POST" else None))
        return httpx.Response(200, json={"id": "search-1"})

    args = parser().parse_args(["search", "artist album", "--extended", "--wait", "0"])
    with _client(handler) as client:
        result = execute(args, client)
    assert result == {"search": {"id": "search-1"}, "responses": []}
    assert calls == [("POST", "/api/soulseek/searches", {"query": "artist album", "mode": "extended"})]


@pytest.mark.parametrize(
    "snapshots",
    [
        [
            {"search": {"state": "InProgress", "fileCount": 4}, "responses": []},
            {"search": {"state": "InProgress", "fileCount": 7}, "responses": []},
            {"search": {"state": "TimedOut, Completed", "fileCount": 2}, "responses": []},
            {"search": {"state": "Completed", "fileCount": 2}, "responses": [{"files": [{"filename": "a"}, {"filename": "b"}]}]},
        ],
        [{"search": {"status": "Cancelled"}, "responses": []}],
        [{"search": {"state": "Completed", "fileCount": 0}, "responses": []}],
    ],
)
def test_search_polls_until_terminal_results_are_available(monkeypatch, snapshots):
    monkeypatch.setenv("MMM_PASSWORD", "test-secret")
    now = [0.0]
    monkeypatch.setattr("app.cli.time.monotonic", lambda: now[0])
    monkeypatch.setattr("app.cli.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    polls = iter(snapshots)
    posts = []

    def handler(request):
        if request.url.path == "/api/login":
            return _login_response(request)
        if request.method == "POST":
            posts.append(json.loads(request.read()))
            return httpx.Response(200, json={"id": "search-1"})
        return httpx.Response(200, json=next(polls))

    args = parser().parse_args(["search", "artist album", "--wait", "10"])
    with _client(handler) as client:
        result = execute(args, client)
    assert result == snapshots[-1]
    assert posts == [{"query": "artist album"}]


def test_search_caps_final_sleep_at_remaining_deadline(monkeypatch):
    monkeypatch.setenv("MMM_PASSWORD", "test-secret")
    now = [0.0]
    sleeps = []
    polls = []
    monkeypatch.setattr("app.cli.time.monotonic", lambda: now[0])

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr("app.cli.time.sleep", sleep)

    def handler(request):
        if request.url.path == "/api/login":
            return _login_response(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "search-1"})
        polls.append(request.url.path)
        assert request.extensions["timeout"]["read"] <= 3
        now[0] += 1.5
        return httpx.Response(200, json={"search": {"state": "InProgress"}, "responses": []})

    args = parser().parse_args(["search", "artist album", "--wait", "3"])
    with _client(handler) as client:
        result = execute(args, client)
    assert result == {"search": {"state": "InProgress"}, "responses": []}
    assert sleeps == [1.5]
    assert polls == ["/api/soulseek/searches/search-1"]


def test_search_read_timeout_returns_last_snapshot_without_cancelling(monkeypatch):
    monkeypatch.setenv("MMM_PASSWORD", "test-secret")
    requested = []

    def handler(request):
        if request.url.path == "/api/login":
            return _login_response(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "search-timeout"})
        requested.append(request)
        assert request.extensions["timeout"]["read"] <= 5
        raise httpx.ReadTimeout("poll timeout", request=request)

    args = parser().parse_args(["search", "artist album", "--wait", "5"])
    with _client(handler) as client:
        result = execute(args, client)
    assert result == {"search": {"id": "search-timeout"}, "responses": []}
    assert len(requested) == 1


def test_invalid_wait_does_not_post_search(monkeypatch):
    monkeypatch.setenv("MMM_PASSWORD", "test-secret")
    calls = []

    def handler(request):
        if request.url.path == "/api/login":
            return _login_response(request)
        calls.append(request.url.path)
        return httpx.Response(200, json={})

    args = parser().parse_args(["search", "artist album", "--wait", "121"])
    with _client(handler) as client:
        with pytest.raises(RuntimeError, match="between 0 and 120"):
            execute(args, client)
    assert calls == []
