"""Tests for the bot's command dispatch, status text, and start-up guards.

The push and the allowlist are covered elsewhere. What was left (79%) is the
``_answer`` dispatcher — which command renders which card, and that a refresh
bypasses the cache — plus the status card, the panel's failure paths, and the
``_save`` cleanup that must not leave a half-written state file behind.
Everything is stubbed; nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest  # noqa: F401

from goldbot.config import Settings
from goldbot.pipeline import Result
from goldbot.telegram.bot import Bot
from goldbot.telegram.client import TelegramError


class _Recorder:
    """A Telegram stand-in that records sends and can be told to fail."""

    def __init__(self, send_ok=True, send_raises=None):
        self.sent: list[tuple] = []
        self.calls: list[tuple] = []
        self._send_ok = send_ok
        self._send_raises = send_raises

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
        return []


def _bot(rec, chat="555", **over):
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = Bot(Settings(telegram_chat=chat, **over), state_path=path, tg=rec)
    bot._log = lambda m: None
    return bot


def _result(**over):
    base = {
        "generated_at": 0,
        "symbol": "XAUUSD",
        "facts": {"confluence": {"bias": "neutral"}, "timeframes": {}},
        "plan": {"direction": "none"},
        "news": {},
        "spot": {"close": 4050.0},
    }
    base.update(over)
    return Result(**base)


# ── _answer: command dispatch ───────────────────────────────────────────


def test_analyse_renders_the_smc_card():
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _result()
    bot._answer("555", "analyse")
    assert rec.sent and "قراءة SMC" in rec.sent[-1][1]


def test_plan_asks_for_the_ai_layer():
    rec = _Recorder()
    bot = _bot(rec)
    seen: list[bool] = []

    def spy(with_ai=True, with_news=True, max_age=None):
        seen.append(with_ai)
        return _result(plan={"direction": "buy", "entry": 1, "stop": 0.9, "target": 1.3, "rr": 3})

    bot.pipeline.run = spy
    bot._answer("555", "plan")
    assert seen == [True]


def test_plan_button_withholds_the_idea_during_a_blackout():
    """The exact bypass that existed: the push honoured the rule, the button did not."""
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _result(
        plan={"direction": "buy", "entry": 4050, "stop": 4044, "target": 4068, "rr": 3},
        news={"blackout": {"active": True, "reason": "CPI"}},
    )
    bot._answer("555", "plan")
    text = rec.sent[-1][1]
    assert "4050" not in text and "4044" not in text and "4068" not in text
    assert "محجوب" in text


def test_news_renders_the_news_card():
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _result(news={"blackout": {"reason": "CPI"}})
    bot._answer("555", "news")
    assert rec.sent and "الأخبار" in rec.sent[-1][1]


def test_status_renders_the_status_text():
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _result(sources={"H1": "kraken"})
    bot._answer("555", "status")
    assert rec.sent and "XAUUSD" in rec.sent[-1][1]


def test_an_unknown_command_gets_a_hint():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("555", "nonsense")
    assert rec.sent and "غير معروف" in rec.sent[-1][1]


def test_refresh_bypasses_the_cache():
    rec = _Recorder()
    bot = _bot(rec)
    ages: list = []

    def spy(with_ai=True, with_news=True, max_age=None):
        ages.append(max_age)
        return _result()

    bot.pipeline.run = spy
    bot._answer("555", "refresh")
    assert ages == [0.0]


def test_help_re_pins_the_panel():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("555", "help")
    assert len(rec.sent) == 2, "help must send the card and then the panel"


def test_menu_renders_the_panel_and_answers_the_spinner():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("555", "menu", callback_id="CB1")
    assert ("answer", "CB1") in rec.calls
    assert len(rec.sent) == 2


def test_settings_renders_without_a_market_read():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("555", "settings")
    assert rec.sent and "الحساسية" in rec.sent[-1][1]


def test_a_telegram_failure_during_a_read_is_swallowed():
    rec = _Recorder(send_raises=TelegramError("boom"))
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _result()
    bot._answer("555", "analyse")  # must not raise


# ── _status_text ────────────────────────────────────────────────────────


def test_status_text_lists_sources_and_spot_trust():
    bot = _bot(_Recorder())
    text = bot._status_text(
        _result(sources={"H1": "kraken"}, spot={"max_divergence_pct": 0.1, "trusted": True})
    )
    assert "kraken" in text
    assert "موثوق" in text


def test_status_text_flags_an_untrusted_spot():
    bot = _bot(_Recorder())
    text = bot._status_text(_result(spot={"max_divergence_pct": 9.0, "trusted": False}))
    assert "مشكوك" in text


def test_status_text_counts_errors():
    bot = _bot(_Recorder())
    text = bot._status_text(_result(errors=["a", "b"]))
    assert "أخطاء: 2" in text


def test_status_text_without_spot_or_errors_is_still_readable():
    bot = _bot(_Recorder())
    text = bot._status_text(_result(spot=None, errors=[]))
    assert "XAUUSD" in text


# ── install_panel ───────────────────────────────────────────────────────


def test_install_panel_sends_the_keyboard():
    rec = _Recorder()
    assert _bot(rec).install_panel() is True
    assert rec.sent and rec.sent[-1][2] is not None


def test_install_panel_logs_and_returns_false_without_a_chat():
    bot = _bot(_Recorder(), chat="")
    lines: list[str] = []
    bot._log = lines.append
    assert bot.install_panel() is False
    assert any("TELEGRAM_CHAT_ID" in line for line in lines)


def test_install_panel_survives_a_telegram_error():
    bot = _bot(_Recorder(send_raises=TelegramError("nope")))
    lines: list[str] = []
    bot._log = lines.append
    assert bot.install_panel() is False
    assert any("تعذّر" in line for line in lines)


# ── _save: the atomic state file ────────────────────────────────────────


def test_save_writes_the_state_and_leaves_no_temp_file():
    bot = _bot(_Recorder())
    bot._last_key = "buy:X"
    bot._save()
    assert os.path.exists(bot.state_path)
    assert not os.path.exists(f"{bot.state_path}.tmp")
    with open(bot.state_path, encoding="utf-8") as fh:
        assert json.load(fh)["last_key"] == "buy:X"


def test_save_persists_the_timeframe_and_rr_overrides():
    bot = _bot(_Recorder())
    bot._tf_override = True
    bot._rr_override = True
    bot._save()
    with open(bot.state_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    assert "timeframes" in payload and "min_rr" in payload


def test_save_survives_an_unwritable_path(tmp_path):
    """A save failure must never crash the loop; the next tick retries."""
    bot = _bot(_Recorder())
    bot.state_path = "/proc/definitely/not/writable/state.json"
    bot._save()  # must not raise


# ── run_forever start-up guards ─────────────────────────────────────────


def test_start_health_is_a_no_op_without_a_port(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    bot = _bot(_Recorder())
    bot._start_health()
    assert getattr(bot, "_started_at", None) is None


def test_start_health_rejects_a_non_numeric_port(monkeypatch):
    monkeypatch.setenv("PORT", "not-a-number")
    bot = _bot(_Recorder())
    lines: list[str] = []
    bot._log = lines.append
    bot._start_health()
    assert any("PORT غير صالح" in line for line in lines)


def test_health_status_reports_uptime_and_cycles():
    bot = _bot(_Recorder())
    bot._started_at = 0
    bot._cycles = 7
    status = bot.health_status()
    assert status["cycles"] == 7
    assert status["status"] == "ok"
    assert status["uptime_seconds"] >= 0
