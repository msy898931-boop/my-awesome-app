"""Tests for the loop itself: the cadence, the health port, and the guards.

These are the paths that only run in production — the ``run_forever`` loop, the
``PORT`` handling, and the self-ping wiring — and they were the reason
``bot.py`` sat at 79%. The loop is driven one iteration at a time by stopping it
with a sentinel exception, so no test ever sleeps or spins.

Everything is stubbed; nothing here touches the network.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from goldbot.config import Settings
from goldbot.pipeline import Result
from goldbot.telegram.bot import Bot
from goldbot.telegram.client import TelegramError


class _Recorder:
    """A Telegram stand-in: records sends, and can be told to fail or feed."""

    def __init__(self, send_ok=True, send_raises=None, updates=None):
        self.sent: list[tuple] = []
        self.calls: list[tuple] = []
        self._send_ok = send_ok
        self._send_raises = send_raises
        self._updates = updates or []

    def send(self, chat_id, text, markdown=True, keyboard=None):
        if self._send_raises is not None:
            raise self._send_raises
        self.sent.append((chat_id, text, keyboard))
        return self._send_ok

    def answer_callback(self, callback_id, text=""):
        self.calls.append(("answer", callback_id))
        return True

    def typing(self, chat_id):
        self.calls.append(("typing", chat_id))
        return True

    def set_commands(self, commands):
        self.calls.append(("set_commands", list(commands)))
        return True

    def updates(self, offset=None, timeout=0):
        return list(self._updates)


def _bot(rec, chat="555", **over):
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = Bot(Settings(telegram_chat=chat, **over), state_path=path, tg=rec)
    bot._log = lambda m: None
    return bot


def _buy(spot=4046.0):
    return Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": spot},
        plan={
            "direction": "buy",
            "entry": 4046.31,
            "stop": 4040.24,
            "target": 4061.73,
            "setup": "CHoCH + FVG",
        },
    )


# ── tick: the first pass primes and says nothing ─────────────────────────


def test_the_first_tick_primes_and_sends_nothing():
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _buy()
    bot.tick()
    assert rec.sent == [], "a restart replayed an alert"
    assert bot._primed is True
    assert bot._last_key == "buy:CHoCH + FVG"


def test_the_first_tick_persists_the_seeded_key():
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _buy()
    bot.tick()
    import json

    with open(bot.state_path, encoding="utf-8") as fh:
        assert json.load(fh)["last_key"] == "buy:CHoCH + FVG"


# ── tick: the send-failure paths ─────────────────────────────────────────


def test_a_send_that_raises_is_logged_and_does_not_propagate():
    rec = _Recorder(send_raises=TelegramError("blocked"))
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _buy()
    lines: list[str] = []
    bot._log = lines.append
    bot._primed = True
    bot._last_key = "different"
    bot.tick()  # must not raise
    assert any("فشل إرسال فكرة" in line for line in lines)


def test_a_send_that_returns_false_is_not_reported_as_sent():
    """The bug this covers: an unconditional "sent" log made a rejected push
    look delivered, so a 401 was invisible in the log."""
    rec = _Recorder(send_ok=False)
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _buy()
    lines: list[str] = []
    bot._log = lines.append
    bot._primed = True
    bot._last_key = "different"
    bot.tick()
    assert any("رفض تيليجرام" in line for line in lines)
    assert not any("أُرسلت فكرة" in line for line in lines)


# ── handle_updates: the malformed-update guards ──────────────────────────


def test_an_update_with_no_text_is_ignored():
    rec = _Recorder(updates=[{"update_id": 1, "message": {"chat": {"id": "555"}}}])
    bot = _bot(rec)
    bot.handle_updates()
    assert rec.sent == []


def test_an_update_with_no_chat_is_ignored():
    rec = _Recorder(updates=[{"update_id": 1, "message": {"text": "/حالة"}}])
    bot = _bot(rec)
    bot.handle_updates()
    assert rec.sent == []


def test_an_unauthorized_message_is_logged_and_dropped():
    rec = _Recorder(updates=[{"update_id": 1, "message": {"chat": {"id": "999"}, "text": "hi"}}])
    bot = _bot(rec, chat="555")
    lines: list[str] = []
    bot._log = lines.append
    bot.handle_updates()
    assert rec.sent == []
    assert any("غير مصرّحة" in line for line in lines)


# ── run_forever: the loop, driven one iteration at a time ────────────────


class _StopLoop(Exception):
    """Raised by a stub to break out of ``while True`` after one pass."""


def _run_one_iteration(bot, monkeypatch, *, handle_raises=None):
    """Run the loop body exactly once by making ``time.monotonic`` stop it.

    The second call to ``monotonic`` is the ``if time.monotonic() < next_cycle``
    check; raising there exits the loop before it can spin.
    """
    calls = {"n": 0}
    real_monotonic = __import__("time").monotonic

    def fake_monotonic():
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _StopLoop
        return real_monotonic()

    monkeypatch.setattr("goldbot.telegram.bot.time.monotonic", fake_monotonic)
    if handle_raises is not None:
        bot.handle_updates = lambda **k: (_ for _ in ()).throw(handle_raises)
    with pytest.raises(_StopLoop):
        bot.run_forever()


def test_run_forever_survives_a_telegram_failure_and_keeps_going(monkeypatch):
    """A failed getUpdates must be logged, not fatal: the loop sleeps and the
    next pass may succeed."""
    monkeypatch.delenv("PORT", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    lines: list[str] = []
    bot._log = lines.append
    slept: list[float] = []
    monkeypatch.setattr("goldbot.telegram.bot.time.sleep", slept.append)

    _run_one_iteration(bot, monkeypatch, handle_raises=TelegramError("down"))
    assert any("تحديثات: فشل" in line for line in lines)
    assert slept == [2]


def test_run_forever_logs_a_bad_cycle_and_continues(monkeypatch):
    """A cycle that raises must be caught: one bad read is not a dead bot."""
    monkeypatch.delenv("PORT", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    lines: list[str] = []
    bot._log = lines.append
    bot.tick = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))

    _run_one_iteration(bot, monkeypatch)
    assert any("خطأ" in line and "boom" in line for line in lines)


def test_run_forever_publishes_commands_and_the_panel(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    _run_one_iteration(bot, monkeypatch)
    kinds = [c[0] for c in rec.calls]
    assert "set_commands" in kinds
    assert rec.sent, "the persistent panel was never sent"


def test_run_forever_logs_a_quiet_checkpoint_every_twenty_cycles(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    lines: list[str] = []
    bot._log = lines.append
    bot.tick = lambda *a, **k: None

    clock = {"t": 0.0}
    monkeypatch.setattr("goldbot.telegram.bot.time.monotonic", lambda: clock["t"])
    calls = {"n": 0}

    def handle(**k):
        calls["n"] += 1
        # Advance well past the interval so every pass runs a cycle, and stop
        # the loop on the 21st pass — after cycle 20 has been recorded.
        clock["t"] += 1000.0
        if calls["n"] >= 21:
            raise _StopLoop

    bot.handle_updates = handle
    with pytest.raises(_StopLoop):
        bot.run_forever()
    assert any("دورة 20: لا جديد" in line for line in lines)
    assert bot._cycles == 20


def test_run_forever_skips_the_cycle_until_it_is_due(monkeypatch):
    """The loop must not re-run the market read on every long-poll return: it
    checks the clock and only cycles when the interval has elapsed."""
    monkeypatch.delenv("PORT", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    ticks: list[int] = []
    bot.tick = lambda *a, **k: ticks.append(1)

    clock = {"t": 0.0}
    monkeypatch.setattr("goldbot.telegram.bot.time.monotonic", lambda: clock["t"])
    calls = {"n": 0}

    def handle(**k):
        calls["n"] += 1
        if calls["n"] >= 3:
            raise _StopLoop
        if calls["n"] == 2:
            # Inside the interval (poll defaults to 60s), so the loop must skip
            # the cycle this pass rather than re-read the market.
            clock["t"] = 10.0

    bot.handle_updates = handle
    with pytest.raises(_StopLoop):
        bot.run_forever()
    assert len(ticks) == 1, "the market was re-read before the interval elapsed"


# ── _start_health: the port and the self-ping wiring ─────────────────────


def test_start_health_opens_the_port_and_wires_the_self_ping(monkeypatch):
    monkeypatch.setenv("PORT", "8099")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://goldbot.example")
    rec = _Recorder()
    bot = _bot(rec)
    started: dict = {}
    pinged: list[str] = []
    import goldbot.health as health

    monkeypatch.setattr(
        health, "start_health_server", lambda port, status: started.update(port=port)
    )
    monkeypatch.setattr(health, "start_self_ping", lambda url: pinged.append(url))

    bot._start_health()
    assert started["port"] == 8099
    assert pinged == ["https://goldbot.example"]
    assert bot._cycles == 0


def test_start_health_says_so_when_the_self_ping_url_is_missing(monkeypatch):
    """A silent skip would look like a healthy run while the service idles out."""
    monkeypatch.setenv("PORT", "8099")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    rec = _Recorder()
    bot = _bot(rec)
    lines: list[str] = []
    bot._log = lines.append
    import goldbot.health as health

    monkeypatch.setattr(health, "start_health_server", lambda port, status: None)

    bot._start_health()
    assert any("RENDER_EXTERNAL_URL" in line for line in lines)
