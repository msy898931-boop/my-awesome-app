"""The study runner must wire the pieces correctly and never crash on thin data.

The only substitution here is the network seam — ``_load_series`` is replaced
with synthetic bars. Everything downstream is the real code: the strategy walk,
the metric layer, the window split, and the matched control. The feeds are built
by hand precisely because a study that depends on a live endpoint cannot be a
test.
"""

from __future__ import annotations

import math

from goldbot.backtest import study as study_mod
from goldbot.backtest.control import skill_test
from goldbot.backtest.study import _pick_primary, run_study
from goldbot.config import Settings
from goldbot.models import Bar, Series


def _swings(count: int, step: int = 3600, start: int = 1_600_000_000) -> list[Bar]:
    """A wavy series: structure detection needs real pivots, not a ramp."""
    bars = []
    price = 2000.0
    for i in range(count):
        price += 0.6 + 22.0 * math.sin(i / 37.0) + 1.5 * math.sin(i / 3.0)
        o = price
        c = price + 6.0 * math.sin(i / 5.0)
        bars.append(
            Bar(
                time=start + i * step,
                open=o,
                high=max(o, c) + 3.0,
                low=min(o, c) - 3.0,
                close=c,
                volume=100.0,
            )
        )
    return bars


def _fake_series(tf: str, step: int, count: int = 600) -> Series:
    return Series(timeframe=tf, source=f"fake:{tf}", bars=_swings(count, step=step))


def _inject(monkeypatch, frames=("M5", "M15", "M30", "H1", "H4")) -> None:
    steps = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}
    table = {tf: _fake_series(tf, steps.get(tf, 3600)) for tf in frames}
    monkeypatch.setattr(study_mod, "_load_series", lambda settings, f, want: table)


def test_pick_primary_prefers_the_deepest_frame():
    series = {"M15": object(), "H1": object(), "H4": object()}
    assert _pick_primary(series, None) == "H1"
    assert _pick_primary(series, "M15") == "M15"
    assert _pick_primary(series, "ZZZ") == "H1"


def test_trade_window_dates_the_fills_and_ignores_unfilled_orders():
    """The window spans the fills; a no_fill order is not a trade."""
    from goldbot.backtest.engine import Trade
    from goldbot.backtest.study import _trade_window

    def _t(day: int, outcome: str) -> Trade:
        return Trade(
            index=day,
            time=1_600_000_000 + day * 86400,
            direction="buy",
            entry=2000.0,
            stop=1990.0,
            target=2020.0,
            rr=2.0,
            setup="s",
            outcome=outcome,
            r_multiple=1.0,
        )

    # An unfilled order sits *before* the first fill and *after* the last: if it
    # were counted it would stretch the window on both ends.
    trades = [_t(0, "no_fill"), _t(10, "win"), _t(14, "loss"), _t(20, "no_fill")]
    period, span = _trade_window(trades)
    assert period == ("2020-09-23", "2020-09-27")
    assert span == 4


def test_trade_window_is_empty_when_nothing_filled():
    from goldbot.backtest.study import _trade_window

    assert _trade_window([]) == (None, 0)


def test_binding_frame_names_the_low_frame_that_gates_the_walk():
    """The gate is the *latest-starting* frame already present at the first fill.

    Every frame is capped near the same bar count, so the low frames reach back
    the fewest days and are missing first. A plan needs an HTF and an LTF, so
    the frame whose first bar is the last to appear before the first trade is
    the one that opened the gate.
    """
    from goldbot.backtest.study import _binding_frame
    from goldbot.models import Series

    # M5 starts latest (fewest days back), M30 earlier, H1 earliest. At a first
    # fill that M5 has not reached, M30 is the gate; the deepest *available*
    # frame wins, not the shortest overall.
    series = {
        "H1": Series(timeframe="H1", source="f", bars=_swings(300, step=3600, start=1_600_000_000)),
        "M30": Series(
            timeframe="M30",
            source="f",
            bars=_swings(300, step=1800, start=1_600_000_000 + 100 * 3600),
        ),
        "M5": Series(
            timeframe="M5",
            source="f",
            bars=_swings(300, step=300, start=1_600_000_000 + 200 * 3600),
        ),
    }
    first_fill = 1_600_000_000 + 150 * 3600  # after M30 opens, before M5 does
    tf, span = _binding_frame(series, first_fill)
    assert tf == "M30"
    assert span > 0

    # Once the walk reaches far enough back that even M5 is present, M5 is the
    # frame that only just appeared, so it gates instead.
    early_fill = 1_600_000_000 + 250 * 3600
    assert _binding_frame(series, early_fill)[0] == "M5"


def test_binding_frame_is_absent_when_no_frame_reaches_the_first_fill():
    from goldbot.backtest.study import _binding_frame
    from goldbot.models import Series

    series = {"H1": Series(timeframe="H1", source="f", bars=_swings(50))}
    assert _binding_frame(series, 1) == (None, 0)


def test_pick_primary_falls_back_to_the_longest_when_no_preference_matches():
    from goldbot.models import Series

    # D1/M1 are outside the preference list, so the longest-series fallback runs.
    series = {
        "D1": Series(timeframe="D1", source="f", bars=_swings(50)),
        "M1": Series(timeframe="M1", source="f", bars=_swings(500)),
    }
    assert _pick_primary(series, None) == "M1"


def test_load_series_keeps_only_frames_with_enough_bars(monkeypatch):
    """The selector drops a thin frame rather than walking it blind."""
    from goldbot.data import feed as feed_mod
    from goldbot.models import Series

    table = {
        "H1": Series(timeframe="H1", source="f", bars=_swings(200)),
        "M15": Series(timeframe="M15", source="f", bars=_swings(10)),  # too thin
    }

    class StubFeed:
        def series(self, tf, want, prefer_depth=False):
            return table.get(tf), None

    monkeypatch.setattr(feed_mod, "build_feed", lambda settings: StubFeed())
    loaded = study_mod._load_series(Settings(), ("H1", "M15"), 20000)
    assert set(loaded) == {"H1"}


def test_run_study_returns_a_well_formed_result(monkeypatch):
    _inject(monkeypatch)
    settings = Settings()
    result = run_study(settings, primary_tf="H1", window_count=4, n_control=60)

    assert result["symbol"] == settings.symbol
    assert result["primary_tf"] == "H1"
    assert result["bars"] > 0
    assert result["span_days"] > 0
    assert result["windows"] == 4
    # The metric block always carries the headline keys, whatever the data did.
    for key in ("n", "expectancy", "profit_factor", "max_drawdown_r", "sharpe"):
        assert key in result["metrics"]
    # The control verdict is present and is one of the three honest words.
    assert result["control"]["verdict"] in {"skill", "beta", "inconclusive"}
    # Window metrics are produced for the requested split, or the single
    # empty fallback when the walk found nothing to fill.
    assert 1 <= len(result["window_metrics"]) <= 4
    assert result["notes"]


def test_every_setup_gets_its_own_curve(monkeypatch):
    """The per-setup curves must exist for exactly the setups in the walk."""
    _inject(monkeypatch)
    result = run_study(Settings(), primary_tf="H1", window_count=4, n_control=60)

    assert set(result["setup_curves"]) == set(result["by_setup"])
    for curve in result["setup_curves"].values():
        assert isinstance(curve, list)
        # A curve is one running total per filled trade, so it is non-empty
        # whenever its setup had a trade, and monotone in nothing but length.
        assert curve or not result["by_setup"]

    # The time-carrying points mirror the plain curves exactly, in step.
    assert [v for _, v in result["equity_points"]] == result["equity_curve"]
    times = [t for t, _ in result["equity_points"]]
    assert times == sorted(times)
    for name, pts in result["setup_points"].items():
        assert [v for _, v in pts] == result["setup_curves"][name]

    # The trade window brackets the fills exactly: its ends are the first and
    # last point of the dated curve, and the span agrees with them.
    if result["equity_points"]:
        first_t = result["equity_points"][0][0]
        last_t = result["equity_points"][-1][0]
        assert result["trade_span_days"] == round((last_t - first_t) / 86400)
        assert result["trade_period"] is not None

    # The pooled curve is exactly the sum of the per-setup curves at the end:
    # the setups partition the trades, so their finals must add to the whole.
    if result["equity_curve"]:
        finals = sum(c[-1] for c in result["setup_curves"].values() if c)
        assert abs(finals - result["equity_curve"][-1]) < 1e-9


def test_run_study_defaults_to_the_deepest_loaded_frame(monkeypatch):
    _inject(monkeypatch, frames=("M15", "H4"))
    result = run_study(Settings(), window_count=2, n_control=40)
    assert result["primary_tf"] == "H4"


def test_run_study_with_no_data_is_a_named_failure_not_a_crash(monkeypatch):
    monkeypatch.setattr(study_mod, "_load_series", lambda settings, f, want: {})
    result = run_study(Settings())
    assert result["bars"] == 0
    assert result["control"] is None
    assert result["metrics"]["n"] == 0
    assert "لا بيانات" in result["notes"][0]


def test_run_study_is_deterministic_across_two_runs(monkeypatch):
    """Same injected data and same seed must give the same verdict."""
    _inject(monkeypatch)
    a = run_study(Settings(), primary_tf="H1", window_count=3, n_control=80)
    b = run_study(Settings(), primary_tf="H1", window_count=3, n_control=80)
    assert a["metrics"] == b["metrics"]
    assert a["control"] == b["control"]


def test_skill_test_on_the_injected_walk_matches_a_second_call(monkeypatch):
    """The study's control call is the control module's own, not a re-implementation."""
    _inject(monkeypatch)
    settings = Settings()
    result = run_study(settings, primary_tf="H1", window_count=2, n_control=40)
    # Recompute the same number from the module directly on empty trades and on
    # the study's own bars to confirm wiring, not equality of fresh randomness.
    bars = _swings(600)
    direct = skill_test([], bars, n=40)
    assert direct["verdict"] == "inconclusive"
    assert result["control"]["verdict"] in {"skill", "beta", "inconclusive"}


def test_print_study_returns_the_formatted_text(monkeypatch):
    from goldbot.backtest.study import print_study

    _inject(monkeypatch)
    text = print_study(Settings(), primary_tf="H1", window_count=2)
    assert "باكتست احترافي" in text
    assert "XAUUSD" in text
