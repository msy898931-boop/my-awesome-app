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
    MIN_STOP_PCT,
    _best_zone,
    _candidate,
    _opposing_liquidity,
    _stop_for,
    _try_bos_continuation,
    _try_choch_retest,
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


# ── setup 1: CHoCH + FVG/OB retest ───────────────────────────────────────


def test_choch_retest_builds_the_highest_quality_setup():
    """The setup the docstring calls the best entry, built end to end.

    A bullish CHoCH on the LTF confirms the higher-frame bias, price returns to
    the demand gap below it, and the opposing pool pays for the risk.
    """
    frames = {
        "H4": _frame(),
        "M15": _frame(
            close=4060.0,
            atr=12.0,
            event={"type": "CHoCH", "direction": "bullish"},
            gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": False}],
            swing_high=4075.0,
            swing_low=4045.0,
        ),
    }
    plan = pick_setup(_facts(frames), 2.0)
    assert plan.is_actionable
    assert plan.direction == "buy"
    assert plan.setup.startswith("CHoCH")
    assert plan.entry == 4050.0, "entry is the near edge of the zone"
    assert plan.stop < plan.entry < plan.target
    assert plan.score == 4, "base 3 plus the discount-zone bonus"


def test_choch_retest_needs_the_higher_frame_agreement():
    """A bullish CHoCH under a bearish H4 must not be traded long."""
    frames = {
        "H4": _frame(trend="bearish"),
        "M15": _frame(
            close=4060.0,
            event={"type": "CHoCH", "direction": "bullish"},
            gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": False}],
        ),
    }
    assert _try_choch_retest(frames, "buy", 2.0) is None


def test_choch_retest_needs_the_matching_event_direction():
    frames = {
        "H4": _frame(),
        "M15": _frame(
            close=4060.0,
            event={"type": "CHoCH", "direction": "bearish"},
            gaps=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "filled": False}],
        ),
    }
    assert _try_choch_retest(frames, "buy", 2.0) is None


def test_an_order_block_is_a_zone_too():
    f = _frame(
        close=4060.0,
        blocks=[{"side": "bullish", "top": 4050.0, "bottom": 4048.0, "mitigated": False}],
    )
    zone = _best_zone(f, "buy", 12.0)
    assert zone is not None and zone[2] == "OB"


def test_a_sell_zone_above_price_is_used():
    f = _frame(
        trend="bearish",
        close=4060.0,
        gaps=[{"side": "bearish", "top": 4070.0, "bottom": 4068.0, "filled": False}],
    )
    zone = _best_zone(f, "sell", 12.0)
    assert zone is not None
    entry, _stop, kind, far_edge = zone
    assert entry == 4068.0 and kind == "FVG" and far_edge == 4070.0


# ── the stop floor and the target gate ───────────────────────────────────


def test_a_tight_anchor_is_pushed_out_to_the_floor():
    """A stop inside the noise is widened, so the R:R is not flattered."""
    stop = _stop_for("buy", 4000.0, 3999.9, atr=10.0)
    assert stop is not None and stop < 4000.0
    assert 4000.0 - stop >= 4000.0 * MIN_STOP_PCT - 1e-9


def test_a_wrong_side_anchor_is_clamped_to_the_floor():
    """An anchor on the wrong side of entry is ignored; the floor still holds.

    ``_stop_for`` takes the *tighter* of the anchor and the floor for a buy, so
    an anchor above entry cannot widen the stop past it — it is simply not used.
    """
    stop = _stop_for("buy", 4000.0, 4010.0, atr=10.0)
    assert stop is not None and stop < 4000.0
    assert 4000.0 - stop == 4000.0 * MIN_STOP_PCT


def test_opposing_liquidity_skips_a_pool_too_close_to_pay():
    """The nearest pool is not the target if it does not clear the R:R."""
    frames = {
        "M15": {
            "liquidity": {
                "equal_highs": [{"price": 4002.0}, {"price": 4020.0}],
                "equal_lows": [],
            },
            "last_swing_high": None,
        }
    }
    target = _opposing_liquidity(frames, "buy", entry=4000.0, stop=3990.0, min_rr=2.0)
    assert target == 4020.0, "the 4002 pool is only 0.2R away and must be skipped"


def test_opposing_liquidity_is_none_when_nothing_pays():
    frames = {"M15": {"liquidity": {"equal_highs": [{"price": 4002.0}]}, "last_swing_high": None}}
    assert _opposing_liquidity(frames, "buy", entry=4000.0, stop=3990.0, min_rr=2.0) is None


def test_sell_target_comes_from_the_lows_ahead():
    frames = {
        "M15": {
            "liquidity": {"equal_lows": [{"price": 3980.0}], "equal_highs": []},
            "last_swing_low": None,
        }
    }
    assert _opposing_liquidity(frames, "sell", entry=4000.0, stop=4010.0, min_rr=2.0) == 3980.0


def test_an_over_far_target_is_refused():
    """An R:R past ``MAX_RR`` is not a better trade; the pool is unreachable."""
    frame = _frame(close=4000.0, swing_high=4200.0, swing_low=3990.0)
    frame["liquidity"] = {"equal_highs": [{"price": 4200.0}], "equal_lows": []}
    plan = _candidate(
        "buy",
        4000.0,
        3990.0,
        10.0,
        {"M15": frame},
        2.0,
        setup="x",
        reason="",
        score=1,
        factors=[],
        timeframe="M15",
    )
    assert plan is None


# ── no frames, neutral bias ──────────────────────────────────────────────


def test_no_frames_yields_an_empty_plan():
    plan = pick_setup({"timeframes": {}}, 2.0)
    assert plan.direction == "none" and plan.rejected


def test_a_neutral_bias_tries_both_directions():
    """A neutral read opens both sides; a biased one only opens its own."""
    frames = {"M15": _frame()}
    plan = pick_setup(_facts(frames, bias="neutral"), 2.0)
    assert any(r.startswith("buy:") for r in plan.rejected)
    assert any(r.startswith("sell:") for r in plan.rejected)


def test_a_counter_bias_direction_is_refused_up_front():
    """In a bullish bias the sell side is rejected before it is built."""
    frames = {"M15": _frame()}
    plan = pick_setup(_facts(frames, bias="bullish"), 2.0)
    assert any("sell: مرفوض" in r for r in plan.rejected)
    assert not any(r.startswith("buy: مرفوض") for r in plan.rejected)


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
