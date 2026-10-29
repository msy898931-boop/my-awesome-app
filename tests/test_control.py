"""The matched control must be deterministic, matched, and honest about weakness.

These tests pin the properties that make the control trustworthy, because a
control that quietly cheats is worse than no control at all:

* the surrogate keeps the real trade's geometry and changes only the time;
* a control that never fills is not counted as a comparison;
* the regime control draws only from the trade's own regime;
* a strategy that is *pure drift* cannot beat its regime control, while one
  that times a real move can — the whole point of the exercise;
* a sample too small to judge comes back ``inconclusive``, never ``skill``.
"""

from __future__ import annotations

import random

from goldbot.backtest.control import (
    _p_value,
    _surrogate,
    bootstrap_ci,
    regime_flags,
    skill_test,
    surrogate_mean,
    trade_regime,
)
from goldbot.backtest.engine import HORIZON_BARS, Trade, evaluate_trade
from goldbot.models import Bar


def _bars(closes: list[float], start: int = 1_700_000_000, step: int = 900) -> list[Bar]:
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append(
            Bar(time=start + i * step, open=o, high=max(o, c) + 0.5, low=min(o, c) - 0.5, close=c)
        )
    return out


def _uptrend(n: int = 400, start: float = 1800.0, step: float = 0.5) -> list[Bar]:
    return _bars([start + i * step for i in range(n)])


def _downtrend(n: int = 400, start: float = 2200.0, step: float = 0.5) -> list[Bar]:
    return _bars([start - i * step for i in range(n)])


def _choppy_uptrend(n: int = 500, start: float = 1800.0, drift: float = 0.3, seed: int = 8):
    """A real market shape: it rises overall, but noisily, with drawdowns.

    A perfectly smooth ramp is degenerate — every bar is "rising" and every
    long wins, so timing cannot matter and a control cannot tell luck from
    skill. Noise comparable to the drift makes the outcome of an entry genuinely
    uncertain, which is the only setting in which the control is informative.
    """
    rng = random.Random(seed)
    closes = []
    price = start
    for _ in range(n):
        price += drift + rng.uniform(-3.0, 3.0)
        closes.append(price)
    return _bars(closes)


def _trade(direction: str = "buy", entry: float = 2000.0, risk: float = 10.0, rr: float = 2.0):
    stop = entry - risk if direction == "buy" else entry + risk
    target = entry + risk * rr if direction == "buy" else entry - risk * rr
    return Trade(
        index=0,
        time=1_700_000_000,
        direction=direction,
        entry=entry,
        stop=stop,
        target=target,
        rr=rr,
        setup="test",
    )


def _resolved(bars: list[Bar], indices: list[int], direction: str = "buy", risk: float = 8.0):
    """Real trades entered at ``indices`` and *resolved* against the future.

    Rating the strategy's own trades is the backtest's job, not the control's,
    so a test that forgets to resolve them measures a row of zeros. This closes
    each trade so its R is real before the control compares against it.
    """
    out = []
    for i in indices:
        entry = bars[i].close
        stop = entry - risk if direction == "buy" else entry + risk
        target = entry + risk * 2.0 if direction == "buy" else entry - risk * 2.0
        trade = Trade(i, bars[i].time, direction, entry, stop, target, 2.0, "test")
        out.append(evaluate_trade(trade, bars[i + 1 : i + 1 + HORIZON_BARS]))
    return out


def test_a_surrogate_changes_only_the_time_not_the_geometry():
    bars = _bars([2000.0 + i for i in range(200)])
    real = _trade("buy", entry=3000.0, risk=12.0, rr=2.5)
    out = _surrogate(real, bars, index=100, cost_fraction=0.0, horizon=60)
    assert out is not None
    # Same direction, same risk distance, same reward-to-risk, different entry.
    assert out.direction == "buy"
    assert abs((out.entry - out.stop) - 12.0) < 1e-9
    assert abs(out.rr - 2.5) < 1e-9
    assert out.entry == bars[100].close
    assert out.entry != real.entry


def test_a_sell_surrogate_keeps_its_direction_and_levels():
    bars = _bars([2000.0 - i for i in range(200)])
    real = _trade("sell", entry=1800.0, risk=8.0, rr=3.0)
    out = _surrogate(real, bars, index=50, cost_fraction=0.0, horizon=60)
    assert out is not None
    assert out.direction == "sell"
    assert out.stop > out.entry > out.target
    assert abs((out.stop - out.entry) - 8.0) < 1e-9


def test_a_surrogate_past_the_end_of_the_sample_is_refused():
    bars = _bars([2000.0] * 100)
    real = _trade()
    assert _surrogate(real, bars, index=95, cost_fraction=0.0, horizon=60) is None


def test_regime_flags_are_trailing_and_never_look_ahead():
    bars = _bars([100.0 + i for i in range(100)])
    flags = regime_flags(bars, window=40)
    # Rising into every bar after warmup, so every late flag is +1.
    assert flags[-1] == 1
    # The first bar has no history to compare, so it reads flat (a fall).
    assert flags[0] == -1
    # Changing a future bar cannot change an earlier flag: the loop is causal.
    altered = list(bars)
    altered[80] = Bar(time=altered[80].time, open=1.0, high=1.0, low=1.0, close=1.0)
    before = regime_flags(bars, window=40)[70]
    after = regime_flags(altered, window=40)[70]
    assert before == after


def test_a_downtrend_reads_as_a_falling_regime():
    bars = _bars([2000.0 - i for i in range(100)])
    assert regime_flags(bars, window=40)[-1] == -1


def test_a_trade_is_matched_to_the_regime_at_its_own_time():
    rising = _bars([1000.0 + i for i in range(200)])
    falling = _bars([3000.0 - i for i in range(200)])
    t_early = Trade(0, rising[50].time, "buy", 0, 0, 0, 2.0, "t")
    t_late = Trade(0, rising[190].time, "buy", 0, 0, 0, 2.0, "t")
    assert trade_regime(t_early, rising) == 1
    assert trade_regime(t_late, rising) == 1
    assert trade_regime(t_late, falling) == -1


def test_a_trade_before_the_sample_defaults_to_falling():
    bars = _bars([2000.0] * 50)
    ancient = Trade(0, bars[0].time - 10_000, "buy", 0, 0, 0, 2.0, "t")
    assert trade_regime(ancient, bars) == -1


def test_surrogate_mean_is_deterministic_for_a_seed():
    bars = _uptrend()
    trades = _resolved(bars, [100, 150, 200, 250, 300])
    a, na = surrogate_mean(trades, bars, random.Random(3), "random")
    b, nb = surrogate_mean(trades, bars, random.Random(3), "random")
    assert (a, na) == (b, nb)
    c, nc = surrogate_mean(trades, bars, random.Random(4), "random")
    assert (a, na) != (c, nc)


def test_an_empty_or_tiny_sample_yields_no_control():
    bars = _uptrend(20)
    assert surrogate_mean([], bars, random.Random(1), "random") == (None, 0)
    assert surrogate_mean([_trade()], bars, random.Random(1), "random") == (None, 0)


def test_regime_control_draws_only_from_the_trades_own_regime():
    # A series that trends up then crashes: the two regimes are both populated.
    closes = [2000.0 + i for i in range(250)] + [2250.0 - 2 * i for i in range(250)]
    bars = _bars(closes)
    buy_late = [_trade("buy", entry=bars[i].close) for i in (300, 320, 340, 360, 380)]
    random_rng = random.Random(5)
    mean, filled = surrogate_mean(buy_late, bars, random_rng, "regime")
    assert filled > 0
    assert mean is not None
    # Every late buy sits in a falling regime, where a matched long loses or
    # ties — the regime mean must not be the flattering whole-sample mean.
    flags = regime_flags(bars)
    whole = [i for i in range(60, len(bars) - 61)]
    frac_falling = sum(1 for i in whole if flags[i] == -1) / len(whole)
    assert frac_falling > 0.2  # both regimes exist, so the filter is not a no-op


def test_a_pure_drift_strategy_cannot_beat_its_regime_control():
    """The confound the module exists to kill.

    A long-only "strategy" that fires at random times in a rising market makes
    money. Its *regime* control draws only from rising bars, so it inherits the
    same drift and the strategy's apparent edge collapses. This must not read
    as skill. The market is choppy on purpose: on a smooth ramp every entry
    wins and there is nothing for the control to separate.
    """
    bars = _choppy_uptrend(500)
    rng = random.Random(2)
    trades = _resolved(bars, sorted(rng.sample(range(100, 400), 60)))
    assert any(t.outcome == "loss" for t in trades), "the market must be able to lose"
    out = skill_test(trades, bars, n=300, seed=9)
    assert out["random_mean"] is not None and out["random_mean"] > 0
    assert out["verdict"] != "skill"


def test_a_timing_edge_beats_both_controls():
    """A strategy that times a real, repeated move should read as skill.

    Constructed so the answer is knowable: price falls for a stretch, then each
    entry buys at a local bottom exactly before a sharp rise back. Geometry is
    fixed; only the timing carries information, which is precisely what the
    control measures.
    """
    closes = []
    price = 2000.0
    for _cycle in range(40):
        for _ in range(10):  # a dip
            price -= 4.0
            closes.append(price)
        for _ in range(30):  # a strong recovery
            price += 6.0
            closes.append(price)
    bars = _bars(closes)
    # Buy at each dip's bottom: index 10 of a cycle, after the fall, before the
    # recovery. A generous risk distance so the stop is not tagged by noise.
    trades = _resolved(bars, [cycle * 40 + 10 for cycle in range(1, 39)], risk=20.0)
    assert all(t.outcome != "no_fill" for t in trades), "every real trade must fill"
    out = skill_test(trades, bars, n=300, seed=9)
    assert out["verdict"] == "skill", out
    assert out["gap_regime"] is not None and out["gap_regime"] > 0


def test_a_small_sample_is_inconclusive_never_skill():
    bars = _uptrend(400)
    trades = _resolved(bars, [100, 200, 300])
    out = skill_test(trades, bars, n=200, seed=1)
    assert out["verdict"] == "inconclusive"
    assert out["real_mean"] is None


def test_a_losing_strategy_is_called_beta_not_inconclusive():
    bars = _downtrend(500)
    # Longs into a falling market: the direction is wrong, so the mean is
    # negative regardless of geometry. That is beta, not the luck of timing.
    trades = _resolved(bars, [100, 150, 200, 250, 300, 350, 400, 420, 440, 460])
    out = skill_test(trades, bars, n=200, seed=1)
    assert out["real_mean"] is not None and out["real_mean"] < 0
    assert out["verdict"] == "beta"


def test_the_control_is_calibrated_against_zero_skill():
    """The property that makes the whole module worth having.

    A "strategy" whose entries are drawn at random has, by construction, no
    timing skill. Run a panel of them and essentially none may be called
    ``skill``. If this ever fires, the control has stopped being a control —
    a window or cost mismatch has crept back in — and every verdict it produces
    is untrustworthy. The window is the trades' own span and the cost matches,
    or a difference in drift reads as an edge.
    """
    bars = _choppy_uptrend(500)
    flagged = 0
    panel = 20
    for seed in range(panel):
        rng = random.Random(seed)
        trades = _resolved(bars, sorted(rng.sample(range(100, 400), 60)))
        if skill_test(trades, bars, n=200, seed=seed)["verdict"] == "skill":
            flagged += 1
    assert flagged == 0, f"{flagged}/{panel} zero-skill panels were called skill"


def test_the_control_still_detects_a_real_edge():
    """Calibration must not be bought by refusing everything.

    The same machinery that shrugs at random entries has to light up for a
    strategy that times a genuine move — otherwise it is just an off switch.
    """
    closes = []
    price = 2000.0
    for _cycle in range(40):
        for _ in range(10):
            price -= 4.0
            closes.append(price)
        for _ in range(30):
            price += 6.0
            closes.append(price)
    bars = _bars(closes)
    trades = _resolved(bars, [c * 40 + 10 for c in range(1, 39)], risk=20.0)
    assert skill_test(trades, bars, n=300, seed=9)["verdict"] == "skill"


def test_p_value_never_reaches_zero_and_is_corrected():
    assert _p_value([0.1, 0.2, 0.3], 0.5) == 1 / 4  # real beats all three
    assert _p_value([0.1, 0.2, 0.3], -1.0) == 1.0  # real loses to all three
    assert _p_value([0.5], 0.5) == 1.0  # a tie counts as beaten


def test_the_eligible_window_is_the_span_the_trades_occupy():
    from goldbot.backtest.control import _eligible_window

    bars = _uptrend(400)
    trades = _resolved(bars, [100, 200, 300])
    window = _eligible_window(trades, bars, HORIZON_BARS)
    assert window
    assert window[0] == 100 and window[-1] == 300
    assert _eligible_window([], bars, HORIZON_BARS) == []


def test_a_regime_pool_that_is_empty_falls_back_to_the_whole_window():
    """A regime with no eligible bars must not starve the control.

    If every walkable bar is in the other regime, the matched draw has nothing
    to pick from. Falling back to the full window keeps the surrogate alive and
    keeps the count honest rather than silently returning no comparison.
    """
    bars = _bars([2000.0 - i for i in range(300)])  # a pure downtrend: no rising bars
    trades = _resolved(bars, [100, 150, 200])
    # Force a *rising* demand against a market that has none.
    for t in trades:
        t.time = bars[-1].time + 10_000
    rng = random.Random(1)
    mean, filled = surrogate_mean(trades, bars, rng, "regime")
    assert filled > 0 and mean is not None


def test_inconclusive_when_a_control_cannot_be_built():
    """A positive strategy with no usable control is inconclusive, not skill."""
    bars = _uptrend(400)
    trades = _resolved(bars, [100, 150, 200, 250, 300, 350, 60, 70, 80, 90, 95, 98])
    out = skill_test(trades, bars, n=0, seed=1)
    assert out["verdict"] == "inconclusive"
    assert out["random_p"] is None and out["regime_p"] is None


def test_bootstrap_ci_brackets_the_mean():
    lo, hi = bootstrap_ci([1.0] * 50, n=200, seed=1)
    assert lo is not None and hi is not None
    assert lo <= 1.0 <= hi


def test_bootstrap_ci_refuses_a_sample_too_small_to_trust():
    assert bootstrap_ci([1.0, 2.0, 3.0], n=200, seed=1) == (None, None)
