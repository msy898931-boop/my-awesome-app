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
