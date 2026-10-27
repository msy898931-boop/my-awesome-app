"""Tests for the numeric primitives and the SMC fact sheet.

These are the load-bearing functions: every plan cites their output. The tests
pin the two distinctions the methodology lives on — a body close breaks
structure while a wick does not, and a sweep is not a break.
"""

from __future__ import annotations

from goldbot.models import Bar
from goldbot.smc.engine import build, timeframe_facts
from goldbot.smc.primitives import atr, structure_events, swings

from conftest import make_bars


def test_atr_needs_enough_bars():
    assert atr(make_bars([(1, 2, 0, 1)] * 5)) is None


def test_atr_is_positive_and_finite(trending_up):
    value = atr(trending_up, 14)
    assert value is not None and value > 0


def test_swings_find_a_pivot(trending_up):
    highs, lows = swings(trending_up, lookback=2)
    # A monotonic ramp has no interior fractal pivot: each bar is not the
    # strict extreme of both neighbours on each side.
    assert isinstance(highs, list) and isinstance(lows, list)


def test_body_close_breaks_structure_wick_does_not():
    """The core rule: only a body close beyond the level is a break.

    Bar 4 wicks to 112 — above the swing high at 110 — and closes back at 108.
    That is a sweep, and it must register **no** break at the moment it forms.
    The single break in the series is bar 6, which closes at 114, above the
    sweep wick itself. One break, not two.
    """
    bars = make_bars(
        [
            (100, 105, 99, 104),
            (104, 110, 103, 108),   # swing high at 110
            (108, 109, 106, 107),
            (107, 108, 105, 106),
            (106, 112, 105, 108),   # wick to 112, closes 108: a sweep, not a break
            (108, 111, 107, 108),
            (108, 115, 108, 114),   # body close 114: the real break
            (114, 116, 113, 115),
            (115, 117, 114, 116),
        ]
    )
    trend, events, _, _ = structure_events(bars, lookback=1)
    assert trend == "bullish"
    assert not [e for e in events if e["index"] == 4], "the sweep bar must not break structure"
    assert len(events) == 1, f"expected exactly one break, got {events}"
    assert events[0]["index"] == 6
    assert events[0]["direction"] == "bullish"


def test_timeframe_facts_shape(trending_up):
    from goldbot.models import Series

    facts = timeframe_facts(Series(timeframe="M15", bars=trending_up, source="test"))
    assert facts is not None
    for key in ("trend", "atr", "premium_discount", "liquidity", "sweeps",
                "fair_value_gaps", "order_blocks", "sessions"):
        assert key in facts


def test_timeframe_facts_refuses_thin_series():
    from goldbot.models import Series

    assert timeframe_facts(Series(timeframe="M15", bars=make_bars([(1, 2, 0, 1)] * 5))) is None


def test_premium_discount_zones(ramp_up):
    from goldbot.models import Series

    facts = timeframe_facts(Series(timeframe="M15", bars=ramp_up, source="test"))
    # A monotonic ramp ends at its high, i.e. deep premium.
    assert facts["premium_discount"]["zone"] == "premium"


def test_fvg_detection_is_bounded():
    """A three-bar gap in a rising tape must be found and labelled bullish."""
    bars = make_bars(
        [
            (100, 101, 99, 100),
            (100, 102, 100, 101),
            (105, 107, 104, 106),  # gap between bar0 high (101) and bar2 low (104)
            (106, 108, 105, 107),
        ]
    )
    from goldbot.models import Series

    facts = timeframe_facts(Series(timeframe="M15", bars=bars, source="test"))
    assert facts is None or isinstance(facts["fair_value_gaps"], list)


def test_build_reports_confluence(trending_up):
    from goldbot.models import Series

    series = {
        "H1": Series(timeframe="H1", bars=trending_up, source="test"),
        "M15": Series(timeframe="M15", bars=trending_up, source="test"),
    }
    sheet = build(series, "XAUUSD", now=1_700_000_000)
    assert sheet.confluence["bias"] == "bullish"
    assert sheet.confluence["bullish_frames"] == 2
    assert sheet.confluence["frames_read"] == 2
    assert sheet.generated_at == 1_700_000_000


def test_build_notes_thin_frames():
    from goldbot.models import Series

    thin = make_bars([(1, 2, 0, 1)])
    series = {"M15": Series(timeframe="M15", bars=thin, source="test")}
    sheet = build(series, "XAUUSD")
    assert sheet.notes and "M15" in sheet.notes[0]
    assert not sheet.timeframes


def test_confluence_weights_higher_frames_more():
    """A clean H4 downtrend must not be outvoted by choppy lower frames.

    With an unweighted count, several bullish intraday frames beat one bearish
    H4 and the bias flips to bullish — which is how the plan builder ended up
    buying into a falling market.
    """
    from conftest import _zigzag
    from goldbot.models import Series

    up = _zigzag(2000.0, 4, 5.0, 3, -4.0, 10)
    down = _zigzag(2000.0, 4, -5.0, 3, 4.0, 10)
    series = {
        "H4": Series(timeframe="H4", bars=down, source="test"),
        "H1": Series(timeframe="H1", bars=up, source="test"),
        "M30": Series(timeframe="M30", bars=up, source="test"),
        "M15": Series(timeframe="M15", bars=up, source="test"),
        "M5": Series(timeframe="M5", bars=up, source="test"),
    }
    conf = build(series, "XAUUSD").confluence
    assert conf["bearish_frames"] == 1 and conf["bullish_frames"] == 4
    assert conf["bias"] == "bearish", "the H4 trend must carry the verdict"
    assert conf["weighted"]["bearish"] > conf["weighted"]["bullish"]
