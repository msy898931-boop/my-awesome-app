"""Tests for the configuration layer.

Two things here are worth pinning and were untested: the minimal ``.env``
parser (it touches secrets, so it must never clobber a real environment
variable) and the coercion helpers (junk in the environment must fall back to
the default, not raise).
"""

from __future__ import annotations

import os

import pytest

from goldbot.config import Settings, _flag, _float, _int, _timeframes, load_dotenv


@pytest.fixture
def restored_environ():
    """``load_dotenv`` writes straight to ``os.environ``, so snapshot and restore.

    Without this the keys a test writes would leak into later tests and make
    the suite order-dependent.
    """
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


# ── the .env parser ──────────────────────────────────────────────────────


def test_dotenv_loads_key_values(tmp_path, monkeypatch, restored_environ):
    monkeypatch.delenv("GOLDBOT_SYMBOL", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nGOLDBOT_SYMBOL=EURUSD\n  GOLDBOT_BARS = 99  \n",
        encoding="utf-8",
    )
    load_dotenv(env)
    assert os.environ["GOLDBOT_SYMBOL"] == "EURUSD"
    assert os.environ["GOLDBOT_BARS"] == "99"


def test_dotenv_strips_surrounding_quotes(tmp_path, restored_environ):
    env = tmp_path / ".env"
    env.write_text('A_QUOTED="hello world"\n', encoding="utf-8")
    load_dotenv(env)
    assert os.environ["A_QUOTED"] == "hello world"


def test_dotenv_never_clobbers_a_real_variable(tmp_path, monkeypatch, restored_environ):
    """A shell-exported secret must win over a stale file."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from-shell")
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=from-file\n", encoding="utf-8")
    load_dotenv(env)
    assert os.environ["TELEGRAM_BOT_TOKEN"] == "from-shell"


def test_a_missing_dotenv_is_a_no_op(tmp_path):
    load_dotenv(tmp_path / "does-not-exist.env")  # must not raise


# ── coercion helpers ─────────────────────────────────────────────────────


def test_flag_reads_falsy_words(monkeypatch):
    for word in ("0", "false", "NO", "off", ""):
        monkeypatch.setenv("F", word)
        assert _flag("F") is False, word
    monkeypatch.setenv("F", "1")
    assert _flag("F") is True


def test_flag_uses_the_default_when_absent(monkeypatch):
    monkeypatch.delenv("F_ABSENT", raising=False)
    assert _flag("F_ABSENT", default=False) is False
    assert _flag("F_ABSENT", default=True) is True


def test_int_and_float_fall_back_on_junk(monkeypatch):
    monkeypatch.setenv("N", "not-a-number")
    assert _int("N", 7) == 7
    assert _float("N", 1.5) == 1.5
    monkeypatch.setenv("N", "42")
    assert _int("N", 7) == 42
    monkeypatch.setenv("N", "2.5")
    assert _float("N", 1.5) == 2.5


# ── timeframes ───────────────────────────────────────────────────────────


def test_timeframes_default_without_the_env(monkeypatch):
    monkeypatch.delenv("GOLDBOT_TIMEFRAMES", raising=False)
    assert _timeframes() == ("M5", "M15", "M30", "H1", "H4")


def test_timeframes_are_canonically_ordered_and_filtered(monkeypatch):
    monkeypatch.setenv("GOLDBOT_TIMEFRAMES", "h4, m5 , M15")
    assert _timeframes() == ("M5", "M15", "H4"), "canonical order, not input order"


def test_timeframes_with_no_known_token_falls_back(monkeypatch):
    monkeypatch.setenv("GOLDBOT_TIMEFRAMES", "W1,XX")
    assert _timeframes() == ("M5", "M15", "M30", "H1", "H4")


# ── Settings ─────────────────────────────────────────────────────────────


def test_settings_read_the_environment(monkeypatch):
    monkeypatch.setenv("GOLDBOT_SYMBOL", "XAUUSD")
    monkeypatch.setenv("GOLDBOT_MIN_RR", "3.0")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    s = Settings()
    assert s.symbol == "XAUUSD"
    assert s.min_rr == 3.0
    assert s.ai_enabled is True


def test_ai_is_disabled_without_a_key(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    assert Settings().ai_enabled is False
