"""Tests for the health endpoint and the self-ping that keeps the host awake.

The server test uses a real socket on an ephemeral port: the point of this
module is that something actually answers, which a stubbed handler would not
prove.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from goldbot.health import ping_once, start_health_server


def _free_port() -> int:
    return 0


def test_health_server_answers_health_and_rejects_other_paths():
    server = start_health_server(_free_port(), lambda: {"status": "ok", "cycles": 3})
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as resp:
            assert resp.status == 200
            assert json.loads(resp.read())["cycles"] == 3

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=10) as resp:
            assert resp.status == 200

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/nope", timeout=10)
            raise AssertionError("an unknown path must not answer 200")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_ping_once_requests_the_health_path(monkeypatch):
    seen: list[str] = []

    class _Resp:
        def read(self):
            return b"{}"

    def fake_urlopen(url, timeout=None):
        seen.append(url)
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert ping_once("https://example.onrender.com") is True
    assert seen == ["https://example.onrender.com/health"]


def test_ping_once_tolerates_a_trailing_slash(monkeypatch):
    seen: list[str] = []

    class _Resp:
        def read(self):
            return b"{}"

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: (seen.append(url), _Resp())[1],
    )
    ping_once("https://example.onrender.com/")
    assert seen == ["https://example.onrender.com/health"]


def test_ping_once_returns_false_on_an_unreachable_host(monkeypatch):
    def boom(url, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert ping_once("https://example.onrender.com") is False


def test_ping_once_does_nothing_without_a_url(monkeypatch):
    def boom(url, timeout=None):
        raise AssertionError("must not call out without a URL")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert ping_once("") is False


def test_start_self_ping_does_nothing_without_a_url():
    """A blank URL must not start a thread that pings nothing forever."""
    import threading

    from goldbot.health import start_self_ping

    before = threading.active_count()
    start_self_ping("")
    assert threading.active_count() == before


def test_start_self_ping_starts_a_daemon_thread(monkeypatch):
    """The thread must be a daemon, or a stop signal could never exit."""
    import threading

    import goldbot.health as health

    started: list[dict] = []
    real_thread = threading.Thread

    class _Spy(real_thread):
        def __init__(self, *a, **k):
            started.append(k)
            super().__init__(*a, **k)

    monkeypatch.setattr(health.threading, "Thread", _Spy)
    # The loop body sleeps before its first ping; a long interval means it will
    # not fire during the test, so no network call happens.
    health.start_self_ping("https://example.onrender.com", interval=3600)
    assert started and started[0].get("daemon") is True


def test_the_self_ping_loop_calls_ping_once(monkeypatch):
    """The thread body must actually ping; the interval is shortened to prove it."""
    import time

    import goldbot.health as health

    hits: list[str] = []
    monkeypatch.setattr(health, "ping_once", lambda url: hits.append(url) or True)
    monkeypatch.setattr(health, "SELF_PING_SECONDS", 0.01)

    health.start_self_ping("https://example.onrender.com", interval=0.01)
    deadline = time.time() + 2
    while not hits and time.time() < deadline:
        time.sleep(0.02)
    assert hits, "the loop never pinged"
