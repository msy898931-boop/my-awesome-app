"""Tests for the backtest engine — the outcome rules and the no-look-ahead rule.

These matter more than most: the win rate the bot reports is only meaningful if
the evaluation is honest, so the pessimistic tie-break and the cost charge are
pinned here.
"""

from __future__ import annotations

from goldbot.backtest.engine import COST_FRACTION, Trade, evaluate_trade

from conftest import make_bars


def _trade(direction="buy", entry=100.0, stop=98.0, target=106.0, rr=3.0):
    return Trade(index=0, time=0, direction=direction, entry=entry, stop=stop,
                 target=target, rr=rr, setup="test")


def test_target_hit_first_is_a_win():
    future = make_bars([(100, 101, 99, 100), (101, 107, 100, 106)])
    trade = evaluate_trade(_trade(), future)
    assert trade.outcome == "win"
    assert trade.r_multiple > 0


def test_stop_hit_first_is_a_loss():
    future = make_bars([(100, 100.5, 99.5, 100), (100, 101, 97, 98), (98, 99, 97, 98)])
    trade = evaluate_trade(_trade(), future)
    assert trade.outcome == "loss"
    assert trade.r_multiple < 0


def test_both_in_one_bar_resolves_as_a_loss():
    """OHLC hides the intrabar order, so the pessimistic reading is taken."""
    future = make_bars([(100, 100, 99, 100), (100, 107, 97, 100)])
    trade = evaluate_trade(_trade(), future)
    assert trade.outcome == "loss"


def test_entry_not_touched_is_not_a_trade():
    """A limit order the market never reaches must not become a phantom loss."""
    future = make_bars([(120, 121, 119, 120)] * 5)
    trade = evaluate_trade(_trade(entry=100.0), future)
    assert trade.outcome == "no_fill"


def test_entry_behind_price_fills_when_price_returns():
    future = make_bars([(101, 101.5, 100.5, 101), (101, 101.5, 99.5, 100.2),
                        (100.2, 107, 100, 106)])
    trade = evaluate_trade(_trade(entry=100.0), future)
    assert trade.outcome == "win"


def test_timeout_is_scored_at_the_mark():
    future = make_bars([(100, 100.2, 99.8, 100)] + [(100, 100.5, 99.5, 100.5)] * 10)
    trade = evaluate_trade(_trade(), future)
    assert trade.outcome == "timeout"
    assert trade.bars_held > 0


def test_costs_are_charged_on_a_win():
    future = make_bars([(100, 100.2, 99.9, 100), (100, 107, 100, 106)])
    trade = evaluate_trade(_trade(), future)
    assert trade.r_multiple < trade.rr, "a win must not be reported at full R"


def test_costs_are_charged_on_a_loss():
    future = make_bars([(100, 100.2, 99.9, 100), (100, 100, 97, 98)])
    trade = evaluate_trade(_trade(), future)
    assert trade.r_multiple < -1.0, "a loss must be worse than exactly -1R"


def test_sell_side_evaluation():
    future = make_bars([(100, 100.2, 99.8, 100), (100, 101, 93, 94)])
    trade = evaluate_trade(_trade(direction="sell", entry=100, stop=102, target=94, rr=3.0), future)
    assert trade.outcome == "win"


def test_empty_future_is_no_fill():
    assert evaluate_trade(_trade(), []).outcome == "no_fill"


def test_zero_risk_is_no_fill():
    assert evaluate_trade(_trade(stop=100.0), make_bars([(100, 101, 99, 100)])).outcome == "no_fill"


def test_cost_fraction_is_not_flattering():
    assert 0 < COST_FRACTION < 0.001


def test_slice_caps_every_frame_to_the_same_lookback():
    """The walk must read frames the way the live bot reads them.

    Uncapped, the higher frames carry their whole fetched history, so a
    15-minute walk compares an H4 trend measured over months against an M15
    trend measured over hours. That mismatch alone invents signals.
    """
    from goldbot.backtest.engine import _slice_at
    from goldbot.models import Series

    m15 = make_bars([(100, 101, 99, 100)] * 400)
    h4 = make_bars([(100, 101, 99, 100)] * 400)
    series = {
        "M15": Series(timeframe="M15", bars=m15, source="t"),
        "H4": Series(timeframe="H4", bars=h4, source="t"),
    }
    sliced = _slice_at(series, "M15", 300, bars=220)
    assert len(sliced["M15"].bars) == 220
    assert len(sliced["H4"].bars) <= 220, "higher frames must be capped too"


def test_slice_never_uses_bars_after_the_signal():
    """The walk must not see the future, on any frame."""
    from goldbot.backtest.engine import _slice_at
    from goldbot.models import Series

    bars = make_bars([(100, 101, 99, 100)] * 300)
    series = {
        "M15": Series(timeframe="M15", bars=bars, source="t"),
        "H4": Series(timeframe="H4", bars=bars, source="t"),
    }
    cutoff = bars[150].time
    sliced = _slice_at(series, "M15", 150, bars=220)
    for tf, s in sliced.items():
        assert all(b.time <= cutoff for b in s.bars), f"{tf} saw the future"
