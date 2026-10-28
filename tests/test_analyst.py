"""Tests for the analyst's happy path and its remaining edges.

``test_ai_budget.py`` pins the quota behaviour and ``test_news_ai.py`` the
validators; what was still uncovered (75%) is the path that actually succeeds —
a model answering with valid JSON, the result being built and validated — plus a
handful of one-line branches. ``urlopen`` is replaced with a scripted reply, so
no model is called and the run is deterministic.
"""

from __future__ import annotations

import io
import json
import urllib.error

from goldbot.ai.analyst import (
    Analyst,
    _clamp,
    _error_payload,
    _norm_bias,
    _parse_json,
    _parse_retry_seconds,
    _validate_trade,
)
from goldbot.config import Settings


class _Resp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _reply(content) -> _Resp:
    """A Groq chat-completion body carrying ``content``."""
    return _Resp(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


def _settings(**over) -> Settings:
    base = {"groq_key": "k", "groq_model": "m1", "groq_fallbacks": ()}
    base.update(over)
    return Settings(**base)


def _facts() -> dict:
    return {
        "confluence": {
            "bias": "bullish",
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


SOUND_REPLY = json.dumps(
    {
        "market_state": "السوق في انحياز صاعد.",
        "bias": "bullish",
        "setup": "CHoCH + FVG retest",
        "reasoning": "كسر البنية ثم إعادة اختبار الفجوة.",
        "confidence": 0.62,
        "trade": {"direction": "buy", "entry": 4050, "stop": 4044, "target": 4068, "why": "منطقة"},
        "warnings": ["خبر قريب"],
    }
)


def _stub_call(monkeypatch, analyst, behaviour):
    """Replace the HTTP layer with ``behaviour(model, prompt)``."""
    monkeypatch.setattr(analyst, "_call_model", behaviour)


# ── the happy path ──────────────────────────────────────────────────────


def test_a_valid_reply_is_parsed_and_marked_validated(monkeypatch):
    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, lambda m, p: SOUND_REPLY)
    result = analyst.analyse(_facts(), min_rr=2.0)
    assert result.configured is True and result.validated is True
    assert result.bias == "bullish"
    assert result.setup == "CHoCH + FVG retest"
    assert result.confidence == 0.62
    assert result.trade["direction"] == "buy"
    assert result.trade["rr"] == 3.0
    assert result.warnings == ["خبر قريب"]


def test_the_real_http_call_builds_the_request_and_reads_the_content(monkeypatch):
    """The one path the budget tests skip: a genuine 200 from Groq."""
    import urllib.request

    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        seen["ua"] = req.get_header("User-agent")
        seen["body"] = json.loads(req.data.decode())
        return _reply(SOUND_REPLY)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    analyst = Analyst(_settings())
    result = analyst.analyse(_facts(), min_rr=2.0)
    assert result.trade["direction"] == "buy"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == "m1"
    assert "Bearer k" in seen["auth"]
    assert "Mozilla" in seen["ua"]


def test_an_empty_content_is_a_failure_not_an_empty_answer(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _reply(""))
    result = Analyst(_settings()).analyse(_facts())
    assert result.configured is False
    assert "empty content" in result.error or "تعذّر" in result.error


def test_a_response_with_no_choices_is_a_failure(monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda req, timeout=None: _Resp(json.dumps({"choices": []}).encode()),
    )
    result = Analyst(_settings()).analyse(_facts())
    assert result.configured is False


def test_a_non_json_reply_falls_back(monkeypatch):
    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, lambda m, p: "مرحبا، لا JSON هنا")
    result = analyst.analyse(_facts())
    assert result.configured is False
    assert "JSON" in result.error


def test_a_model_that_raises_a_plain_exception_is_tried_and_then_falls_back(monkeypatch):
    def _boom(model, prompt):
        raise ValueError("kaboom")

    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, _boom)
    # Two attempts, then the fallback with the reason recorded.
    result = analyst.analyse(_facts())
    assert result.configured is False
    assert "ValueError" in result.error


def test_a_non_429_http_error_fails_immediately(monkeypatch):
    def _boom(model, prompt):
        raise urllib.error.HTTPError(
            "https://x", 401, "unauthorized", {}, io.BytesIO(b'{"error":{"message":"bad key"}}')
        )

    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, _boom)
    result = analyst.analyse(_facts())
    assert result.configured is False
    assert "bad key" in result.error


def test_an_out_of_range_confidence_is_clamped(monkeypatch):
    reply = json.dumps({"market_state": "x", "bias": "bullish", "confidence": 7.5})
    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, lambda m, p: reply)
    assert analyst.analyse(_facts()).confidence == 1.0


def test_a_reply_with_a_sound_plan_but_no_confidence_defaults_to_zero(monkeypatch):
    reply = json.dumps({"market_state": "x", "bias": "bullish", "trade": {}})
    analyst = Analyst(_settings())
    _stub_call(monkeypatch, analyst, lambda m, p: reply)
    result = analyst.analyse(_facts())
    assert result.confidence == 0.0
    assert result.trade == {"direction": "none"}


def test_the_fallback_lists_the_frames_it_read():
    result = Analyst(Settings(groq_key="")).analyse(_facts())
    assert "M15" in result.market_state
    assert result.configured is False


# ── small helpers ───────────────────────────────────────────────────────


def test_parse_json_rejects_empty_and_non_dict():
    assert _parse_json("") is None
    assert _parse_json("   ") is None
    assert _parse_json("[1, 2]") is None


def test_parse_json_unwraps_a_brace_pair_inside_prose():
    assert _parse_json('sure: {"a": 1} done') == {"a": 1}


def test_parse_json_returns_none_when_no_braces_exist():
    assert _parse_json("no braces here") is None


def test_norm_bias_accepts_only_the_three_known_values():
    assert _norm_bias("Bullish") == "bullish"
    assert _norm_bias("sideways") == "neutral"
    assert _norm_bias(None) == "neutral"


def test_clamp_handles_a_non_numeric_value():
    assert _clamp("abc", 0.0, 1.0) == 0.0
    assert _clamp(0.5, 0.0, 1.0) == 0.5


def test_error_payload_is_read_once_and_cached():
    exc = urllib.error.HTTPError("https://x", 429, "e", {}, io.BytesIO(b'{"error": {"code": "x"}}'))
    first = _error_payload(exc)
    second = _error_payload(exc)  # the stream is drained; the cache must serve it
    assert first == {"error": {"code": "x"}}
    assert second == first


def test_error_payload_survives_a_body_that_is_not_json():
    exc = urllib.error.HTTPError("https://x", 500, "e", {}, io.BytesIO(b"<html>oops</html>"))
    assert _error_payload(exc) == {}


def test_parse_retry_seconds_reads_a_bare_seconds_form():
    exc = urllib.error.HTTPError(
        "https://x", 429, "e", {}, io.BytesIO(b'{"error": {"message": "try again in 20s"}}')
    )
    assert _parse_retry_seconds(exc) == 20.0


def test_parse_retry_seconds_is_zero_when_no_wait_is_named():
    exc = urllib.error.HTTPError(
        "https://x", 429, "e", {}, io.BytesIO(b'{"error": {"message": "limit reached"}}')
    )
    assert _parse_retry_seconds(exc) == 0.0


# ── _validate_trade edges ───────────────────────────────────────────────


def test_a_none_direction_is_returned_untouched():
    trade, reasons = _validate_trade({"direction": "none"}, 2.0)
    assert trade == {"direction": "none"} and reasons == []


def test_a_missing_price_drops_the_plan():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": None, "target": 110}, 2.0
    )
    assert reasons and "بلا أسعار" in reasons[0]


def test_a_non_numeric_price_drops_the_plan():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": "x", "stop": "y", "target": "z"}, 2.0
    )
    assert reasons and "بلا أسعار" in reasons[0]


def test_a_sell_with_a_target_above_entry_is_dropped():
    _, reasons = _validate_trade(
        {"direction": "sell", "entry": 100, "stop": 102, "target": 105}, 2.0
    )
    assert any("الهدف" in r for r in reasons)


def test_a_buy_with_a_target_below_entry_is_dropped():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": 99, "target": 99.5}, 2.0
    )
    assert any("الهدف" in r for r in reasons)


def test_a_sound_sell_plan_is_accepted():
    trade, reasons = _validate_trade(
        {"direction": "sell", "entry": 100, "stop": 102, "target": 94, "why": "س"}, 2.0
    )
    assert not reasons
    assert trade["direction"] == "sell"
    assert trade["rr"] == 3.0


def test_a_zero_risk_plan_has_no_rr_and_is_dropped():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": 100, "target": 110}, 2.0
    )
    # entry == stop is not "below" the entry, so both the stop and the R:R fail.
    assert len(reasons) >= 1
