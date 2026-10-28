"""Tests for the two setups and the zone selector that had no coverage.

The CHoCH path was the only one exercised, yet BOS continuation and sweep
reversal are two of the three ways a plan is born, and ``_best_zone`` is the
helper whose own docstring calls its mistake "the single most damaging error
this function can make". Each is pinned here so a change that breaks the rule
fails a test rather than the book.
"""

from __future__ import annotations

from goldbot.strategy.plan import (
    MAX_ZONE_ATR,
    _best_zone,
    _try_bos_continuation,
    _try_sweep_reversal,
    pick_setup,
)


def _frame(
    trend="bullish",
    close=4060.0,
    atr=12.0,
    event=None,
    gaps=None,
    blocks=None,
    sweeps=None,
    zone=None,
    swing_low=4045.0,
    swing_high=4110.0,
):
    """A single frame fact-sheet, defaulting to a clean bullish structure."""
    return {
        "trend": trend,
        "last_close": close,
        "atr": atr,
        "last_event": event or {},
        "premium_discount": {"zone": zone or ("discount" if trend == "bullish" else "premium")},
        "liquidity": {
            "equal_highs": [{"price": swing_high, "count": 2, "last_index": 40}],
            "equal_lows": [{"price": swing_low, "count": 2, "last_index": 40}],
        },
        "sweeps": sweeps or [],
        "fair_value_gaps": gaps or [],
        "order_blocks": blocks or [],
        "last_swing_high": swing_high,
        "last_swing_low": swing_low,
    }


def _facts(frames, bias="bullish"):
    return {
        "timeframes": frames,
        "confluence": {"bias": bias, "alignment": 0.66, "frames_read": len(frames)},
    }


# ── _best_zone: the retracement rule ─────────────────────────────────────


def test_buy_zone_above_price_is_refused():
    """A buy zone above price is a chase, not a retracement.

    Selecting the nearest zone regardless of side is the error the docstring
    warns about, so a bullish gap sitting *above* the close must not be used.
    """
    f = _frame(
        close=4060.0,
        gaps=[{"side": "bullish", "top": 4080.0, "bottom": 4078.0, "filled": False}],
    )
    assert _best_zone(f, "buy", 12.0) is None


def test_buy_zone_below_price_is_used():
    f = _frame(
        close=4060.0,
        gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": False}],
    )
    zone = _best_zone(f, "buy", 12.0)
    assert zone is not None
    entry, _stop, kind, far_edge = zone
    assert entry == 4050.0 and kind == "FVG" and far_edge == 4048.0


def test_sell_zone_below_price_is_refused():
    f = _frame(
        trend="bearish",
        close=4060.0,
        gaps=[{"side": "bearish", "top": 4040.0, "bottom": 4038.0, "filled": False}],
    )
    assert _best_zone(f, "sell", 12.0) is None


def test_a_zone_wider_than_the_cap_is_refused():
    """A zone wider than ``MAX_ZONE_ATR`` is the whole range, not a level."""
    wide = 12.0 * MAX_ZONE_ATR + 1.0
    f = _frame(
        close=4060.0,
        gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4050.0 - wide, "filled": False}],
    )
    assert _best_zone(f, "buy", 12.0) is None


def test_filled_and_mitigated_zones_are_skipped():
    f = _frame(
        close=4060.0,
        gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": True}],
        blocks=[{"side": "bullish", "top": 4046.0, "bottom": 4044.0, "mitigated": True}],
    )
    assert _best_zone(f, "buy", 12.0) is None


def test_the_nearest_zone_wins():
    f = _frame(
        close=4060.0,
        gaps=[
            {"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": False},
            {"side": "bullish", "top": 4056.0, "bottom": 4055.0, "filled": False},
        ],
    )
    zone = _best_zone(f, "buy", 12.0)
    assert zone is not None and zone[0] == 4056.0


# ── setup 2: BOS continuation ────────────────────────────────────────────


def test_bos_continuation_enters_on_the_retest():
    """The broken level behind price is the entry, not the break itself."""
    f = _frame(
        event={"type": "BOS", "direction": "bullish", "level": 4055.0},
        swing_low=4045.0,
    )
    plan = _try_bos_continuation({"M15": f}, "buy", "bullish", 2.0)
    assert plan is not None
    assert plan.direction == "buy"
    assert plan.entry == 4055.0, "the entry must be the broken level, not the close"
    assert plan.setup.startswith("استمرار BOS")


def test_bos_continuation_refuses_a_chase():
    """A break above price means price ran away — there is no retest entry."""
    f = _frame(event={"type": "BOS", "direction": "bullish", "level": 4090.0}, close=4060.0)
    assert _try_bos_continuation({"M15": f}, "buy", "bullish", 2.0) is None


def test_bos_continuation_requires_the_matching_bias():
    f = _frame(event={"type": "BOS", "direction": "bullish", "level": 4055.0})
    assert _try_bos_continuation({"M15": f}, "buy", "bearish", 2.0) is None


def test_bos_continuation_needs_a_swing_anchor():
    """Without a swing to hide the stop behind, there is no honest stop."""
    f = _frame(event={"type": "BOS", "direction": "bullish", "level": 4055.0})
    f["last_swing_low"] = None
    assert _try_bos_continuation({"M15": f}, "buy", "bullish", 2.0) is None


# ── setup 3: liquidity sweep reversal ────────────────────────────────────


def test_sweep_reversal_from_discount_buys():
    f = _frame(
        close=4060.0,
        zone="discount",
        sweeps=[{"side": "sell_side_taken", "level": 4050.0}],
    )
    plan = _try_sweep_reversal({"M15": f}, "buy", 2.0)
    assert plan is not None
    assert plan.direction == "buy"
    assert plan.setup == "انعكاس بعد كنس سيولة"


def test_sweep_reversal_needs_the_stretched_zone():
    """A sweep is only a reversal from a stretched part of the range."""
    f = _frame(
        close=4060.0,
        zone="premium",
        sweeps=[{"side": "sell_side_taken", "level": 4050.0}],
    )
    assert _try_sweep_reversal({"M15": f}, "buy", 2.0) is None


def test_sweep_reversal_needs_the_right_side_swept():
    f = _frame(
        close=4060.0,
        zone="discount",
        sweeps=[{"side": "buy_side_taken", "level": 4070.0}],
    )
    assert _try_sweep_reversal({"M15": f}, "buy", 2.0) is None


# ── integration through pick_setup ───────────────────────────────────────


def test_pick_setup_can_build_a_bos_plan_end_to_end():
    frames = {
        "H4": _frame(),
        "H1": _frame(),
        "M15": _frame(event={"type": "BOS", "direction": "bullish", "level": 4055.0}),
    }
    plan = pick_setup(_facts(frames), 2.0)
    assert plan.is_actionable
    assert plan.direction == "buy"
    assert plan.stop < plan.entry < plan.target
    assert plan.rr >= 2.0
