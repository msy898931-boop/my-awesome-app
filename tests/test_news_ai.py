"""Tests for the news layer and the AI guards.

The news tests run against fixtures, not the network. The AI tests assert the
validation and fallback behaviour with no key present, which is the state CI
runs in.
"""

from __future__ import annotations

from goldbot.ai.analyst import Analyst, _parse_json, _validate_trade
from goldbot.config import Settings
from goldbot.news.calendar import Event, _parse, _to_float
from goldbot.news.window import blackout


def _event(
    title="Non-Farm Payrolls",
    country="USD",
    impact="high",
    when=1000,
    forecast=180.0,
    previous=170.0,
    actual=None,
):
    return Event(
        title=title,
        country=country,
        impact=impact,
        time=when,
        forecast=forecast,
        previous=previous,
        actual=actual,
    )


def test_blackout_inactive_with_no_events():
    out = blackout([], now=1000)
    assert out["active"] is False
    assert out["imminent"] == []


def test_blackout_active_around_release():
    out = blackout([_event(when=1000)], now=1000)
    assert out["active"] is True
    assert out["imminent"]


def test_blackout_inactive_far_from_release():
    out = blackout([_event(when=100_000)], now=1000)
    assert out["active"] is False


def test_pending_release_asserts_no_direction():
    read = _event().gold_read()
    assert read["read"] == "pending"
    assert "gold" not in read


def test_no_baseline_asserts_no_direction():
    read = _event(forecast=None, previous=None, actual=180.0).gold_read()
    assert read["read"] == "no_baseline"
    assert "gold" not in read


def test_us_hot_print_pressures_gold():
    read = _event(forecast=180.0, actual=220.0).gold_read()
    assert read["read"] == "direct"
    assert "ضغط" in read["gold"]


def test_us_cold_print_supports_gold():
    read = _event(forecast=180.0, actual=140.0).gold_read()
    assert read["read"] == "direct"
    assert "دعم" in read["gold"]


def test_jobless_claims_invert():
    """More claims is a weaker dollar, which supports gold."""
    read = _event(title="Jobless Claims", forecast=200.0, actual=260.0).gold_read()
    assert "دعم" in read["gold"]


def test_non_us_event_is_indirect():
    read = _event(country="AUD", forecast=1.0, actual=2.0).gold_read()
    assert read["read"] == "indirect"
    assert "gold" not in read


def test_inline_print_is_flat():
    read = _event(forecast=180.0, actual=180.0).gold_read()
    assert read["read"] == "inline"


def test_to_float_tolerates_junk():
    assert _to_float("2.5%") == 2.5
    assert _to_float("") is None
    assert _to_float("-") is None
    assert _to_float(None) is None


def test_parse_rejects_incomplete_rows():
    assert _parse({"title": "", "date": "2024-01-01T00:00:00-05:00"}) is None
    assert _parse({"title": "CPI", "date": None}) is None


def test_parse_accepts_a_valid_row():
    event = _parse(
        {
            "title": "CPI m/m",
            "country": "USD",
            "impact": "High",
            "date": "2024-01-01T08:30:00-05:00",
            "forecast": "0.3%",
            "previous": "0.2%",
        }
    )
    assert event is not None
    assert event.impact == "high"
    assert event.forecast == 0.3


def test_parse_json_handles_fenced_output():
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert _parse_json('text {"a": 2} more') == {"a": 2}
    assert _parse_json("not json at all") is None


def test_validate_trade_drops_wrong_side_stop():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": 101, "target": 110}, 2.0
    )
    assert reasons and "الوقف" in reasons[0]


def test_validate_trade_drops_low_rr():
    _, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": 99, "target": 100.5}, 2.0
    )
    assert reasons and "المخاطرة" in reasons[0]


def test_validate_trade_accepts_a_sound_plan():
    trade, reasons = _validate_trade(
        {"direction": "buy", "entry": 100, "stop": 98, "target": 106, "why": "x"}, 2.0
    )
    assert not reasons
    assert trade["rr"] == 3.0


def test_analyst_falls_back_without_a_key():
    settings = Settings(groq_key="")
    result = Analyst(settings).analyse({"timeframes": {}, "confluence": {"bias": "neutral"}})
    assert result.configured is False
    assert result.market_state
    assert result.error
