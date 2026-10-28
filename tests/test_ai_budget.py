"""Tests for the AI budget guard and the honesty of the fallback card.

The bug these cover: the bot asked the model on every 60-second cycle, whether
or not a card would be sent. That burned a 200k-token *daily* budget in about
an hour and a half, after which every call was a 429 and the card fell back to
a deterministic read. Two things were wrong beyond the burn rate:

  * a daily limit was retried every cycle as if it were a per-minute one, so the
    quota could never recover; and
  * the fallback reported ``confidence`` equal to frame agreement, so a card
    with no AI behind it still showed a confident-looking "61%".
"""

from __future__ import annotations

import io
import json
import time
import urllib.error

import pytest

from goldbot.ai.analyst import (
    Analyst,
    _http_reason,
    _is_daily,
    _parse_retry_seconds,
    _short_rate_message,
)
from goldbot.config import Settings

DAILY_429 = {
    "error": {
        "message": (
            "Rate limit reached for model `openai/gpt-oss-120b` in organization "
            "`org_x` service tier `on_demand` on tokens per day (TPD): Limit 200000, "
            "Used 199416, Requested 3364. Please try again in 20m0.96s."
        ),
        "type": "tokens",
        "code": "rate_limit_exceeded",
    }
}
MINUTE_429 = {
    "error": {
        "message": "Rate limit reached ... on tokens per minute (TPM): Limit 8000. "
        "Please try again in 20s.",
        "code": "rate_limit_exceeded",
    }
}


def _http_error(payload: dict, code: int = 429) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps(payload).encode())
    return urllib.error.HTTPError("https://x", code, "err", {}, body)


def _facts() -> dict:
    return {
        "confluence": {
            "bias": "bullish",
            "alignment": 0.6,
            "bullish_frames": 3,
            "bearish_frames": 2,
            "frames_read": 5,
        },
        "timeframes": {
            "M15": {
                "trend": "bullish",
                "atr": 3.2,
                "last_event": {"type": "CHoCH", "direction": "bullish"},
                "premium_discount": {"zone": "premium"},
            }
        },
    }


# ── reading the rate-limit body ──────────────────────────────────────────


def test_http_reason_returns_detail_and_code():
    detail, code = _http_reason(_http_error(DAILY_429))
    assert code == "rate_limit_exceeded"
    assert "الحصة اليومية" in detail


def test_a_daily_limit_is_told_apart_from_a_minute_limit():
    assert _is_daily(_http_error(DAILY_429)) is True
    assert _is_daily(_http_error(MINUTE_429)) is False


def test_the_named_wait_is_parsed_from_the_message():
    assert _parse_retry_seconds(_http_error(DAILY_429)) == pytest.approx(1200.96)


def test_the_body_is_read_once_so_later_helpers_are_not_starved():
    exc = _http_error(DAILY_429)
    # First call drains the stream; the others must still see the body.
    assert _http_reason(exc)[1] == "rate_limit_exceeded"
    assert _is_daily(exc) is True
    assert _parse_retry_seconds(exc) > 0


def test_the_condensed_reason_names_the_ceiling_and_the_wait():
    msg = _short_rate_message(DAILY_429["error"]["message"])
    assert "الحصة اليومية" in msg and "20m0.96s" in msg


# ── the cooldown ─────────────────────────────────────────────────────────


def test_a_daily_429_parks_the_model_instead_of_retrying_it():
    settings = Settings(groq_key="k", groq_model="m1", groq_fallbacks=())
    analyst = Analyst(settings)

    def limited(model, prompt):
        raise _http_error(DAILY_429)

    analyst._call_model = limited
    with pytest.raises(RuntimeError) as err:
        analyst._call({"confluence": {}}, None)
    assert "نفدت الحصة" in str(err.value)
    assert analyst._parked("m1") is True


def test_a_parked_model_is_not_asked_again():
    settings = Settings(groq_key="k", groq_model="m1", groq_fallbacks=())
    analyst = Analyst(settings)
    analyst._cooldowns["m1"] = time.time() + 600
    calls = []
    analyst._call_model = lambda m, p: calls.append(m) or "{}"
    with pytest.raises(RuntimeError):
        analyst._call({"confluence": {}}, None)
    assert calls == []


def test_an_expired_cooldown_lets_the_model_back_in():
    settings = Settings(groq_key="k", groq_model="m1", groq_fallbacks=())
    analyst = Analyst(settings)
    analyst._cooldowns["m1"] = time.time() - 1
    assert analyst._parked("m1") is False


def test_a_minute_limit_does_not_park_the_model():
    settings = Settings(groq_key="k", groq_model="m1", groq_fallbacks=())
    analyst = Analyst(settings)
    analyst._call_model = lambda m, p: (_ for _ in ()).throw(_http_error(MINUTE_429))
    with pytest.raises(RuntimeError):
        analyst._call({"confluence": {}}, None)
    assert analyst._parked("m1") is False


# ── the fallback card must not fake confidence ───────────────────────────


def test_the_fallback_reports_no_confidence():
    result = Analyst(Settings(groq_key="")).analyse(_facts())
    assert result.configured is False
    assert result.confidence == 0.0
    # The structural agreement is still reported, just not as a probability.
    assert "3 صاعد" in result.market_state


def test_the_fallback_carries_a_readable_reason():
    result = Analyst(Settings(groq_key="")).analyse(_facts())
    assert result.error == "لم يُضبط مفتاح الذكاء الاصطناعي"
