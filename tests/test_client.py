"""Tests for the Telegram Bot API client.

The client is the one place in the project that speaks HTTP, and it was the
least covered module (49%). Nothing here touches the network: ``urlopen`` is
replaced with a scripted fake, so a failure means the client's own contract
broke — the retry, the split, the error translation — not that Telegram was
down.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from goldbot.telegram.client import ALLOWED_UPDATES, Telegram, TelegramError, _chunks


class _Resp:
    """A minimal stand-in for the ``urlopen`` context manager."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _ok(result=None, ok=True, description=None):
    payload = {"ok": ok, "result": result if result is not None else {}}
    if description is not None:
        payload["description"] = description
    return _Resp(json.dumps(payload).encode())


def _http_error(code: int, body: bytes):
    # ``HTTPError`` closes ``fp`` when it is collected and drains it on
    # ``read()``; ``BytesIO`` has both behaviours, so the fake needs no class.
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def calls(monkeypatch):
    """Record every request and answer with a scripted response."""
    import urllib.request

    recorded: list[dict] = []
    queue: list = []

    def fake_urlopen(req, timeout=None):
        recorded.append(
            {
                "url": req.full_url,
                "method": req.method,
                "payload": json.loads(req.data.decode()) if req.data else None,
                "timeout": timeout,
            }
        )
        response = queue.pop(0) if queue else _ok()
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return type("Calls", (), {"recorded": recorded, "queue": queue})


def _client():
    return Telegram("123:abc", timeout=5.0)


# ── construction ────────────────────────────────────────────────────────


def test_a_token_is_required():
    with pytest.raises(TelegramError, match="no bot token"):
        Telegram("")


def test_me_returns_the_result(calls):
    calls.queue.append(_ok(result={"username": "bot"}))
    assert _client().me() == {"username": "bot"}
    assert calls.recorded[-1]["url"].endswith("/getMe")


# ── error translation ───────────────────────────────────────────────────


def test_an_http_error_carries_the_status_and_body(calls):
    calls.queue.append(_http_error(400, b'{"description":"bad entity"}'))
    with pytest.raises(TelegramError, match=r"HTTP 400.*bad entity"):
        _client().me()


def test_a_network_error_is_reported_as_a_network_error(calls):
    calls.queue.append(urllib.error.URLError("dns boom"))
    with pytest.raises(TelegramError, match="network"):
        _client().me()


def test_ok_false_is_raised_with_the_description(calls):
    calls.queue.append(_ok(ok=False, description="chat not found"))
    with pytest.raises(TelegramError, match="chat not found"):
        _client().me()


def test_ok_false_without_a_description_still_raises(calls):
    calls.queue.append(_ok(ok=False))
    with pytest.raises(TelegramError, match="unknown error"):
        _client().me()


# ── send ────────────────────────────────────────────────────────────────


def test_send_refuses_an_empty_chat_id():
    with pytest.raises(TelegramError, match="chat id"):
        _client().send("", "hello")


def test_send_refuses_an_empty_message():
    with pytest.raises(TelegramError, match="empty"):
        _client().send("123", "   ")


def test_send_returns_true_on_success(calls):
    calls.queue.append(_ok())
    assert _client().send("123", "hello") is True
    payload = calls.recorded[-1]["payload"]
    assert payload["chat_id"] == "123"
    assert payload["parse_mode"] == "Markdown"


def test_send_without_markdown_omits_parse_mode(calls):
    calls.queue.append(_ok())
    _client().send("123", "hi", markdown=False)
    assert "parse_mode" not in calls.recorded[-1]["payload"]


def test_a_malformed_entity_is_retried_as_plain_text(calls):
    """A bad Markdown entity is the usual 400; the text must still land."""
    calls.queue.append(_http_error(400, b"can't parse entities"))
    calls.queue.append(_ok())
    assert _client().send("123", "*unclosed") is True
    assert len(calls.recorded) == 2
    assert "parse_mode" in calls.recorded[0]["payload"]
    assert "parse_mode" not in calls.recorded[1]["payload"]


def test_send_returns_false_when_both_attempts_fail(calls):
    calls.queue.append(_http_error(400, b"nope"))
    calls.queue.append(_http_error(400, b"still nope"))
    assert _client().send("123", "hi") is False


def test_a_long_message_is_split_and_the_keyboard_rides_the_last_part(calls):
    for _ in range(3):
        calls.queue.append(_ok())
    keyboard = {"inline_keyboard": [[{"text": "x", "callback_data": "x"}]]}
    assert _client().send("123", "أ" * 9500, keyboard=keyboard) is True
    assert len(calls.recorded) == 3
    assert all("reply_markup" not in c["payload"] for c in calls.recorded[:-1])
    assert calls.recorded[-1]["payload"]["reply_markup"] == keyboard


# ── answer_callback ─────────────────────────────────────────────────────


def test_answer_callback_refuses_an_empty_id(calls):
    assert _client().answer_callback("") is False
    assert calls.recorded == []


def test_answer_callback_includes_the_toast_text(calls):
    calls.queue.append(_ok())
    assert _client().answer_callback("CB1", "تم") is True
    assert calls.recorded[-1]["payload"]["text"] == "تم"


def test_answer_callback_swallows_a_failure(calls):
    calls.queue.append(_http_error(400, b"query is too old"))
    assert _client().answer_callback("CB1") is False


# ── typing ──────────────────────────────────────────────────────────────


def test_typing_refuses_an_empty_chat(calls):
    assert _client().typing("") is False
    assert calls.recorded == []


def test_typing_sends_the_action(calls):
    calls.queue.append(_ok())
    assert _client().typing("123") is True
    assert calls.recorded[-1]["payload"]["action"] == "typing"


def test_typing_swallows_a_failure(calls):
    calls.queue.append(urllib.error.URLError("down"))
    assert _client().typing("123") is False


# ── set_commands ────────────────────────────────────────────────────────


def test_set_commands_refuses_an_empty_list(calls):
    assert _client().set_commands([]) is False
    assert calls.recorded == []


def test_set_commands_pairs_names_with_descriptions(calls):
    calls.queue.append(_ok())
    assert _client().set_commands([("analyse", "تحليل"), ("plan", "صفقة")]) is True
    sent = calls.recorded[-1]["payload"]["commands"]
    assert sent == [
        {"command": "analyse", "description": "تحليل"},
        {"command": "plan", "description": "صفقة"},
    ]


def test_set_commands_swallows_a_failure(calls):
    calls.queue.append(_http_error(400, b"bad"))
    assert _client().set_commands([("a", "b")]) is False


# ── updates ─────────────────────────────────────────────────────────────


def test_updates_returns_an_empty_list_on_error(calls):
    calls.queue.append(urllib.error.URLError("down"))
    assert _client().updates() == []


def test_updates_coerces_a_non_list_result_to_empty(calls):
    """Telegram returns ``{}`` on some paths; callers iterate the result."""
    calls.queue.append(_ok(result={}))
    assert _client().updates() == []


def test_updates_omits_the_offset_when_none(calls):
    calls.queue.append(_ok(result=[]))
    _client().updates(None)
    assert "offset" not in calls.recorded[-1]["payload"]


def test_updates_sends_the_offset_when_given(calls):
    calls.queue.append(_ok(result=[]))
    _client().updates(42)
    assert calls.recorded[-1]["payload"]["offset"] == 42


def test_updates_always_names_allowed_updates(calls):
    calls.queue.append(_ok(result=[]))
    _client().updates()
    sent = calls.recorded[-1]["payload"]["allowed_updates"]
    assert set(sent) == set(ALLOWED_UPDATES)
    assert "callback_query" in sent


def test_updates_returns_the_result_list(calls):
    calls.queue.append(_ok(result=[{"update_id": 1}, {"update_id": 2}]))
    assert [u["update_id"] for u in _client().updates()] == [1, 2]


# ── _chunks ─────────────────────────────────────────────────────────────


def test_chunks_returns_nothing_for_empty_text():
    assert _chunks("", 4000) == []


def test_chunks_hard_splits_one_overlong_line():
    """An unbroken line longer than the limit must not produce an empty piece."""
    parts = _chunks("x" * 9000, 4000)
    assert parts == ["x" * 4000, "x" * 4000, "x" * 1000]
    assert all(parts)


def test_chunks_with_a_nonpositive_size_is_passthrough():
    assert _chunks("hello", 0) == ["hello"]


def test_chunks_keeps_lines_whole_when_they_fit():
    text = "\n".join(["a" * 100] * 5)
    parts = _chunks(text, 250)
    assert "".join(parts) == text
    assert all(len(p) <= 250 for p in parts)
