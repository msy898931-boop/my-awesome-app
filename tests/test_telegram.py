"""Tests for the Telegram client guards.

No network here: the point is that the client refuses bad input *before* it
becomes an opaque 400 from Telegram, which is what an empty chat id used to do.
"""

from __future__ import annotations

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


def _bot(recorder):
    from goldbot.config import Settings
    from goldbot.telegram.bot import Bot

    return Bot(Settings(), tg=recorder)


def test_menu_keyboard_shape_and_ascii_data():
    from goldbot.telegram.bot import menu_keyboard

    kb = menu_keyboard()
    rows = kb["inline_keyboard"]
    assert len(rows) == 2
    buttons = [b for row in rows for b in row]
    assert len(buttons) == 4
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
