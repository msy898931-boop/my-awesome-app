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
