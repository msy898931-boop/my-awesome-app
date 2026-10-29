"""Tests for the supervisor that runs both bots on one free-tier service.

The point of :mod:`goldbot.dual` is a hosting constraint — one ``/health`` on
one ``$PORT`` — so the tests check exactly that seam: one health document that
names both engines, one port opened, and AYZR started only when it has an
identity of its own. The bots are stubbed; nothing here polls or sleeps.
"""

from __future__ import annotations

import pytest

from goldbot import dual
from goldbot.ayzr.config import AyzrSettings
from goldbot.config import Settings


class _StubBot:
    def __init__(self) -> None:
        self.ran: list[dict] = []

    def run_forever(self, **kwargs):
        self.ran.append(kwargs)

    def health_status(self):
        return {"bot": "@stub"}


class _StubAyzr:
    def __init__(self) -> None:
        self.ran: list[dict] = []

    def run_forever(self, **kwargs):
        self.ran.append(kwargs)

    def health_status(self):
        return {"service": "ayzr"}


# ── the shared health document ───────────────────────────────────────────


def test_dual_health_names_both_engines():
    status = dual.DualHealth(_StubBot(), _StubAyzr()).status()
    assert status["status"] == "ok"
    assert status["bot"] == {"bot": "@stub"}
    assert status["ayzr"] == {"service": "ayzr"}


def test_dual_health_reports_a_missing_ayzr_as_none():
    """With no second bot the block is null, not invented — the same rule the
    rest of the project follows: an absent reading is not a fake one."""
    status = dual.DualHealth(_StubBot(), None).status()
    assert status["ayzr"] is None


# ── _open_health: one port for both ──────────────────────────────────────


def test_open_health_serves_the_combined_document(monkeypatch):
    monkeypatch.setenv("PORT", "18081")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://dual.example")
    opened: list[tuple] = []
    pinged: list[str] = []
    monkeypatch.setattr(
        "goldbot.health.start_health_server", lambda port, status: opened.append((port, status))
    )
    monkeypatch.setattr("goldbot.health.start_self_ping", lambda url, *a: pinged.append(url))

    status = dual.DualHealth(_StubBot(), _StubAyzr()).status
    dual._open_health(status)

    assert opened[0][0] == 18081
    assert opened[0][1]()["bot"] == {"bot": "@stub"}
    assert pinged == ["https://dual.example"]


def test_open_health_is_a_noop_without_a_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    called: list[str] = []
    monkeypatch.setattr("goldbot.health.start_health_server", lambda *a: called.append("x"))
    dual._open_health(lambda: {})
    assert called == []


def test_open_health_ignores_a_bad_port(monkeypatch):
    monkeypatch.setenv("PORT", "not-a-port")
    called: list[str] = []
    monkeypatch.setattr("goldbot.health.start_health_server", lambda *a: called.append("x"))
    dual._open_health(lambda: {})
    assert called == []


def test_open_health_skips_the_self_ping_without_a_url(monkeypatch):
    monkeypatch.setenv("PORT", "18082")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    pinged: list[str] = []
    monkeypatch.setattr("goldbot.health.start_health_server", lambda *a: None)
    monkeypatch.setattr("goldbot.health.start_self_ping", lambda url, *a: pinged.append(url))
    dual._open_health(lambda: {})
    assert pinged == []


def test_thread_runs_the_target_and_is_named():
    """The real helper, not a stub: it must run the body and be a daemon, or a
    stopped service would hang on a live thread."""
    seen: list[str] = []
    thread = dual._thread(lambda: seen.append("ran"), "unit")
    thread.join(timeout=5)
    assert seen == ["ran"]
    assert thread.name == "unit"
    assert thread.daemon is True


# ── run(): both loops, one runner, the bot on the main thread ─────────────


def test_run_starts_ayzr_when_it_has_a_token(monkeypatch):
    monkeypatch.setattr("goldbot.dual._open_health", lambda *a: None)
    bot = _StubBot()
    ayzr = _StubAyzr()
    started: list[str] = []
    monkeypatch.setattr("goldbot.dual.Bot", lambda settings: bot)
    monkeypatch.setattr("goldbot.dual.AyzrBot", lambda settings: (started.append("ayzr"), ayzr)[1])
    # Let the thread body run inline so the assertion needs no join.
    monkeypatch.setattr("goldbot.dual._thread", lambda fn, name: (fn(), None)[1])

    rc = dual.run(Settings(telegram_token="t"), AyzrSettings(telegram_token="a"))

    assert rc == 0
    assert started == ["ayzr"]
    # The bot keeps the shadow runner so the journal is written once.
    assert bot.ran == [{"open_health": False, "start_ayzr": True}]
    # AYZR's loop must NOT start a second runner, or every signal lands twice.
    assert ayzr.ran == [{"open_health": False, "start_runner": False}]


def test_run_leaves_the_bot_alone_without_an_ayzr_token(monkeypatch):
    monkeypatch.setattr("goldbot.dual._open_health", lambda *a: None)
    bot = _StubBot()
    monkeypatch.setattr("goldbot.dual.Bot", lambda settings: bot)
    monkeypatch.setattr(
        "goldbot.dual.AyzrBot",
        lambda settings: pytest.fail("AYZR must not start without its own token"),
    )

    rc = dual.run(Settings(telegram_token="t"), AyzrSettings(telegram_token=""))

    assert rc == 0
    assert bot.ran == [{"open_health": False, "start_ayzr": True}]


def test_main_refuses_without_a_token(monkeypatch):
    monkeypatch.setattr("goldbot.dual.Settings", lambda: Settings(telegram_token=""))
    assert dual.main() == 1


def test_main_runs_with_a_token(monkeypatch):
    monkeypatch.setattr("goldbot.dual.Settings", lambda: Settings(telegram_token="t"))
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr("goldbot.dual.run", lambda *a, **k: 0)
    assert dual.main() == 0
