"""Tests for the Telegram client guards.

No network here: the point is that the client refuses bad input *before* it
becomes an opaque 400 from Telegram, which is what an empty chat id used to do.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from goldbot.telegram.client import Telegram, TelegramError, _chunks


def test_send_refuses_empty_chat_id():
    tg = Telegram("123:abc")
    with pytest.raises(TelegramError, match="chat id"):
        tg.send("", "hello")


def test_send_refuses_whitespace_chat_id():
    tg = Telegram("123:abc")
    with pytest.raises(TelegramError, match="chat id"):
        tg.send("   ", "hello")


def test_send_refuses_empty_text():
    tg = Telegram("123:abc")
    with pytest.raises(TelegramError, match="empty"):
        tg.send("123", "")


def test_client_requires_a_token():
    with pytest.raises(TelegramError):
        Telegram("")


def test_chunks_split_on_the_limit_without_losing_text():
    text = "أ" * 9500
    parts = _chunks(text, 4000)
    assert len(parts) == 3
    assert "".join(parts) == text
    assert all(len(p) <= 4000 for p in parts)


def test_chunks_leave_short_text_alone():
    assert _chunks("مرحبا", 4000) == ["مرحبا"]


# ── inline keyboard ─────────────────────────────────────────────────────


class _Recorder:
    """A stand-in Telegram that records calls instead of hitting the network."""

    def __init__(self):
        self.sent: list[tuple] = []
        self.calls: list[tuple] = []

    def updates(self, offset=None, timeout=0):
        return []

    def send(self, chat_id, text, markdown=True, keyboard=None):
        self.sent.append((chat_id, text, keyboard))
        return True

    def answer_callback(self, callback_id, text=""):
        self.calls.append(("answer", callback_id))
        return True

    def typing(self, chat_id):
        self.calls.append(("typing", chat_id))
        return True

    def set_commands(self, commands):
        self.calls.append(("set_commands", list(commands)))
        return True


def _bot(recorder, state_path=None):
    import tempfile

    from goldbot.config import Settings
    from goldbot.telegram.bot import Bot

    if state_path is None:
        state_path = os.path.join(tempfile.mkdtemp(), "state.json")
    return Bot(Settings(), state_path=state_path, tg=recorder)


def test_menu_keyboard_shape_and_ascii_data():
    from goldbot.telegram.bot import menu_keyboard

    kb = menu_keyboard()
    rows = kb["inline_keyboard"]
    assert len(rows) == 3
    buttons = [b for row in rows for b in row]
    assert len(buttons) == 6
    for button in buttons:
        # Telegram caps callback_data at 64 bytes and requires the label to
        # carry the Arabic, so the data itself stays short and ASCII.
        assert len(button["callback_data"].encode()) <= 64
        button["callback_data"].encode("ascii")


def test_pressing_a_button_answers_then_sends_with_keyboard():
    rec = _Recorder()
    bot = _bot(rec)
    bot._handle_callback(
        {"id": "CB1", "data": "status", "message": {"chat": {"id": "999"}}}
    )
    assert ("answer", "CB1") in rec.calls
    assert ("typing", "999") in rec.calls
    assert len(rec.sent) == 1
    chat_id, text, keyboard = rec.sent[0]
    assert chat_id == "999"
    assert text
    assert keyboard is not None and "inline_keyboard" in keyboard


def test_every_menu_button_renders_a_card_with_a_keyboard():
    from goldbot.telegram.bot import MENU

    for _, data in [x for row in MENU for x in row]:
        rec = _Recorder()
        bot = _bot(rec)
        bot._answer("999", data)
        assert len(rec.sent) == 1, data
        _, text, keyboard = rec.sent[0]
        assert text.strip(), data
        assert keyboard is not None, data


def test_arabic_commands_still_map_to_their_latin_button():
    rec = _Recorder()
    bot = _bot(rec)
    for arabic, latin in [("تحليل", "analyse"), ("صفقة", "plan"),
                          ("أخبار", "news"), ("حالة", "status")]:
        rec.sent.clear()
        bot._respond("999", f"/{arabic}")
        assert len(rec.sent) == 1, arabic
        assert rec.sent[0][2] is not None, arabic


def test_unknown_command_sends_a_hint_with_the_menu():
    rec = _Recorder()
    bot = _bot(rec)
    bot._respond("999", "/xyz")
    assert len(rec.sent) == 1
    assert "غير معروف" in rec.sent[0][1]
    assert rec.sent[0][2] is not None


def test_callback_without_a_chat_id_is_ignored():
    rec = _Recorder()
    bot = _bot(rec)
    bot._handle_callback({"id": "CB1", "data": "status"})
    assert rec.sent == []


def test_register_commands_uses_ascii_names():
    rec = _Recorder()
    bot = _bot(rec)
    bot._register_commands()
    entry = next(c for c in rec.calls if c[0] == "set_commands")
    for name, _ in entry[1]:
        name.encode("ascii")  # Telegram rejects non-ASCII command names


# ── settings, refresh and persistence ───────────────────────────────────


def test_settings_button_opens_the_grid_with_current_state():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("999", "settings")
    assert len(rec.sent) == 1
    _, body, keyboard = rec.sent[0]
    assert "الإعدادات" in body
    labels = [b["text"] for row in keyboard["inline_keyboard"] for b in row]
    # every frame appears, plus the three sensitivity options
    for tf in ("M1", "M5", "M15", "M30", "H1", "H4", "D1"):
        assert any(tf in label for label in labels), tf
    assert sum("حساسية" in label or "متوازنة" in label or "صارمة" in label
               for label in labels) == 3


def test_toggling_a_frame_changes_the_settings_and_the_state_file():
    rec = _Recorder()
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = _bot(rec, state_path=path)

    before = bot.settings.timeframes
    assert "D1" not in before
    bot._answer("999", "tf:D1", callback_id="CB")
    assert "D1" in bot.settings.timeframes
    assert ("answer", "CB") in rec.calls

    # persisted, so a restart keeps it
    restarted = _bot(_Recorder(), state_path=path)
    assert "D1" in restarted.settings.timeframes


def test_turning_a_frame_off_is_also_persisted():
    rec = _Recorder()
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = _bot(rec, state_path=path)
    bot._answer("999", "tf:M5")
    assert "M5" not in bot.settings.timeframes
    assert "M5" not in _bot(_Recorder(), state_path=path).settings.timeframes


def test_the_last_frame_cannot_be_turned_off():
    rec = _Recorder()
    bot = _bot(rec)
    for tf in ("M5", "M15", "M30", "H4", "H1"):
        bot._answer("999", f"tf:{tf}")
    assert len(bot.settings.timeframes) == 1
    only = bot.settings.timeframes[0]
    bot._answer("999", f"tf:{only}")
    assert bot.settings.timeframes == (only,)


def test_sensitivity_button_sets_min_rr_and_persists():
    rec = _Recorder()
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = _bot(rec, state_path=path)
    bot._answer("999", "sens:3.0")
    assert bot.settings.min_rr == 3.0
    assert _bot(_Recorder(), state_path=path).settings.min_rr == 3.0


def test_settings_changes_rebuild_the_pipeline():
    rec = _Recorder()
    bot = _bot(rec)
    old = bot.pipeline
    bot._answer("999", "sens:1.5")
    assert bot.pipeline is not old
    assert bot.pipeline.settings.min_rr == 1.5


def test_refresh_bypasses_the_cache():
    rec = _Recorder()
    bot = _bot(rec)
    calls: list[float | None] = []
    real_run = bot.pipeline.run

    def spy(with_ai=True, with_news=True, max_age=None):
        calls.append(max_age)
        return real_run(with_ai=with_ai, with_news=with_news, max_age=max_age)

    bot.pipeline.run = spy
    bot._answer("999", "refresh")
    assert calls == [0.0]
    assert len(rec.sent) == 1
    assert rec.sent[0][2] is not None


def test_back_to_menu_restores_the_main_keyboard():
    rec = _Recorder()
    bot = _bot(rec)
    bot._answer("999", "menu")
    _, body, keyboard = rec.sent[0]
    assert "بوت الذهب" in body or "اضغط" in body
    labels = [b["text"] for row in keyboard["inline_keyboard"] for b in row]
    assert any("تحديث" in label for label in labels)
    assert any("الإعدادات" in label for label in labels)


def test_arabic_settings_and_refresh_commands_work():
    rec = _Recorder()
    bot = _bot(rec)
    for arabic in ("إعدادات", "تحديث"):
        rec.sent.clear()
        bot._respond("999", f"/{arabic}")
        assert len(rec.sent) == 1, arabic
