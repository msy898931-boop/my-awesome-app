"""Tests for the strategy, risk sizing, and the honesty guards.

The guards are the interesting part: the tests assert that the code *refuses*
to emit a trade when the numbers do not support one, because a bot that always
finds a trade is a bot that lies.
"""

from __future__ import annotations

from goldbot.strategy.plan import MAX_RR, build_plan, pick_setup
from goldbot.strategy.risk import position_size


def _facts(direction="buy", entry=4060.0, trend="bullish"):
    frame = {
        "trend": trend,
        "last_close": entry,
        "atr": 12.0,
        "last_event": {"type": "CHoCH", "direction": trend, "level": entry, "time": 1, "index": 50},
        "premium_discount": {"zone": "discount" if trend == "bullish" else "premium"},
        "liquidity": {
            "equal_highs": [{"price": entry + 40.0, "count": 2, "last_index": 40}],
            "equal_lows": [{"price": entry - 40.0, "count": 2, "last_index": 40}],
        },
        "sweeps": [],
        "fair_value_gaps": [
            {
                "side": trend,
                "top": entry + 0.5,
                "bottom": entry - 0.5,
                "filled": False,
                "index": 50,
                "time": 1,
            }
        ],
        "order_blocks": [],
        "last_swing_high": entry + 40.0,
        "last_swing_low": entry - 40.0,
    }
    return {
        "timeframes": {"H4": frame, "H1": frame, "M15": dict(frame, trend=trend)},
        "confluence": {
            "bias": trend,
            "alignment": 0.66,
            "bullish_frames": 3,
            "bearish_frames": 0,
            "frames_read": 3,
        },
    }


def test_no_trade_when_no_frames():
    plan = build_plan({"timeframes": {}, "confluence": {}}, 2.0)
    assert not plan.is_actionable
    assert plan.rejected


def test_flat_market_produces_no_trade(flat_market):
    from goldbot.models import Series
    from goldbot.smc.engine import build as build_facts

    sheet = build_facts({"M15": Series(timeframe="M15", bars=flat_market, source="t")}, "XAUUSD")
    plan = build_plan(sheet.as_dict(), 2.0)
    assert not plan.is_actionable, "a range must not yield a plan"


def test_plan_has_coherent_levels():
    plan = pick_setup(_facts(), 2.0)
    if plan.is_actionable:
        if plan.direction == "buy":
            assert plan.stop < plan.entry < plan.target
        else:
            assert plan.stop > plan.entry > plan.target


def test_rr_is_bounded_above():
    """An absurd R:R must be refused, not reported as a great trade."""
    facts = _facts()
    for frame in facts["timeframes"].values():
        frame["equal_highs"] = [{"price": 5000.0, "count": 2, "last_index": 40}]
        frame["last_swing_high"] = 5000.0
    plan = pick_setup(facts, 2.0)
    assert not plan.is_actionable or plan.rr <= MAX_RR


def test_position_size_arithmetic():
    out = position_size(1000.0, 1.0, 4060.0, 4054.0)
    # $10 risk over $6 per ounce over 100 oz per lot = 0.0167 lots.
    assert out["risk_usd"] == 10.0
    assert out["per_ounce"] == 6.0
    assert out["lots"] == 0.02


def test_position_size_refuses_bad_input():
    out = position_size(1000.0, 1.0, 4060.0, 4060.0)
    assert out["lots"] == 0.0
    assert "غير صالحة" in out["note"]


def test_min_rr_is_enforced():
    """A plan that cannot reach the minimum R:R must not be emitted."""
    plan = pick_setup(_facts(), min_rr=99.0)
    assert not plan.is_actionable


# ── regression guards for the three bugs that made the book lose ─────────


def test_counter_bias_direction_is_refused():
    """A bullish book must not emit a sell, and vice versa.

    The original code tried both directions every time and took whichever
    scored best, so in a downtrend it kept buying. That is the bug that
    produced a 6% win rate on the buy side.
    """
    facts = _facts(trend="bullish")
    facts["confluence"]["bias"] = "bullish"
    plan = pick_setup(facts, 2.0)
    assert plan.direction != "sell", "a bullish bias must never yield a sell"

    facts = _facts(trend="bearish")
    facts["confluence"]["bias"] = "bearish"
    plan = pick_setup(facts, 2.0)
    assert plan.direction != "buy", "a bearish bias must never yield a buy"


def test_counter_bias_refusal_is_explained():
    facts = _facts(trend="bullish")
    facts["confluence"]["bias"] = "bullish"
    plan = pick_setup(facts, 2.0)
    if not plan.is_actionable:
        assert any("يخالف الانحياز" in r for r in plan.rejected), plan.rejected


def test_neutral_bias_leaves_both_sides_open():
    facts = _facts(trend="bullish")
    facts["confluence"]["bias"] = "neutral"
    plan = pick_setup(facts, 2.0)
    assert not any("يخالف الانحياز" in r for r in plan.rejected)
