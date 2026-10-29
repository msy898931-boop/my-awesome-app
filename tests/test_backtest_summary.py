"""Tests for the backtest scoreboard and the walk-forward driver.

``evaluate_trade`` (one trade, one outcome) is pinned in ``test_backtest.py``.
What was uncovered (54%) is everything around it: the aggregation in
``_summarise`` — win rate, profit factor, drawdown, the per-setup buckets — and
``run_backtest``'s own plumbing: no history at all, a frame chosen when M15 is
absent, and the notes that record the run. The AI layer is off by design here,
so this file is fully deterministic.
"""

from __future__ import annotations

from goldbot.backtest.engine import (
    COST_FRACTION,
    HORIZON_BARS,
    BacktestReport,
    Trade,
    _summarise,
    _walk,
)
from goldbot.models import Bar, Series


def _trade(outcome, r, direction="buy", setup="s1"):
    t = Trade(
        index=0,
        time=0,
        direction=direction,
        entry=100.0,
        stop=98.0,
        target=106.0,
        rr=3.0,
        setup=setup,
    )
    t.outcome = outcome
    t.r_multiple = r
    return t


def _report():
    return BacktestReport(symbol="XAUUSD", bars_tested=100, signals=0)


# ── _summarise: the scoreboard ──────────────────────────────────────────


def test_no_trades_is_reported_as_no_signals():
    report = _summarise(_report(), [])
    assert report.signals == 0
    assert any("لا إشارات" in n for n in report.notes)


def test_unfilled_signals_are_excluded_from_the_win_rate():
    """'The market never came to us' is not the same as 'we were wrong'."""
    trades = [_trade("win", 3.0), _trade("no_fill", 0.0), _trade("loss", -1.0)]
    report = _summarise(_report(), trades)
    assert report.no_fill == 1
    assert report.wins == 1 and report.losses == 1
    assert report.win_rate == 0.5  # 1 of 2 decided, not 1 of 3 signals
    assert any("استُثنيت" in n for n in report.notes)


def test_every_signal_unfilled_is_noted_distinctly():
    report = _summarise(_report(), [_trade("no_fill", 0.0), _trade("no_fill", 0.0)])
    assert report.win_rate == 0.0
    assert any("لم تُنفَّذ" in n for n in report.notes)


def test_win_rate_ignores_timeouts_in_the_denominator():
    trades = [_trade("win", 2.0), _trade("timeout", 0.5)]
    report = _summarise(_report(), trades)
    assert report.timeouts == 1
    assert report.win_rate == 1.0


def test_profit_factor_divides_gross_win_by_gross_loss():
    trades = [_trade("win", 2.0), _trade("win", 2.0), _trade("loss", -1.0)]
    report = _summarise(_report(), trades)
    assert report.profit_factor == 4.0
    assert report.total_r == 3.0
    assert report.avg_r == 1.0


def test_profit_factor_is_zero_when_there_are_no_losses():
    report = _summarise(_report(), [_trade("win", 2.0)])
    assert report.profit_factor == 0.0


def test_max_drawdown_is_measured_on_the_cumulative_curve():
    # +1, then -2, -2: peak 1, trough -3, so the worst fall is 4.
    trades = [_trade("win", 1.0), _trade("loss", -2.0), _trade("loss", -2.0)]
    report = _summarise(_report(), trades)
    assert report.max_drawdown_r == -4.0


def test_trades_are_bucketed_by_setup_and_direction():
    trades = [
        _trade("win", 2.0, direction="buy", setup="bos"),
        _trade("loss", -1.0, direction="buy", setup="bos"),
        _trade("win", 3.0, direction="sell", setup="sweep"),
    ]
    report = _summarise(_report(), trades)
    assert report.by_setup["bos"] == {"n": 2, "win_rate": 0.5, "avg_r": 0.5}
    assert report.by_direction["buy"]["n"] == 2
    assert report.by_direction["sell"]["win_rate"] == 1.0


def test_the_cost_and_horizon_are_named_in_the_notes():
    report = _summarise(_report(), [_trade("win", 2.0)])
    assert any("التكلفة" in n and "الأفق" in n for n in report.notes)


def test_the_report_serialises_its_trades():
    report = _summarise(_report(), [_trade("win", 2.0)])
    as_dict = report.as_dict()
    assert as_dict["trades"][0]["outcome"] == "win"


# ── evaluate_trade: the unfilled-timeout edges ──────────────────────────


def test_a_trade_that_fills_on_the_last_window_bar_still_resolves():
    from goldbot.backtest.engine import evaluate_trade

    future = [Bar(time=1000, open=99.0, high=99.0, low=99.0, close=99.0, volume=1.0)] * 5
    # The 6th bar reaches the entry; nothing after it touches stop or target,
    # so the trade times out at the mark rather than being discarded.
    future.append(Bar(time=2000, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0))
    trade = evaluate_trade(_trade("open", 0.0), future, fill_window=6)
    assert trade.outcome == "timeout"


def test_a_fill_with_no_bars_left_scores_zero():
    from goldbot.backtest.engine import evaluate_trade

    future = [Bar(time=1000, open=100.0, high=100.5, low=99.5, close=100.0, volume=1.0)]
    trade = evaluate_trade(_trade("open", 0.0), future)
    assert trade.outcome == "timeout"
    assert trade.r_multiple == 0.0


# ── _walk: the forward walk ─────────────────────────────────────────────


def _bars(n, start=1_000_000, step=900, price=2000.0):
    return [
        Bar(
            time=start + i * step,
            open=price,
            high=price + 5,
            low=price - 5,
            close=price,
            volume=1.0,
        )
        for i in range(n)
    ]


def test_walk_returns_nothing_when_history_is_too_short():
    series = {"M15": Series(timeframe="M15", bars=_bars(10), source="t")}
    assert _walk(series, min_rr=2.0) == []


def test_walk_falls_back_to_the_only_frame_present():
    """A run on H1 alone must walk H1, not crash looking for M15."""
    series = {"H1": Series(timeframe="H1", bars=_bars(400), source="t")}
    # Flat bars produce no plan, so the result is an empty list — but the point
    # is that it returns rather than raising on the missing M15.
    assert _walk(series, min_rr=2.0, primary_tf="M15") == []


# ── run_backtest: the driver ────────────────────────────────────────────


class _Settings:
    symbol = "XAUUSD"
    timeframes = ("M15", "H1")
    min_rr = 2.0
    cache_seconds = 90


def test_run_backtest_without_history_says_so(monkeypatch):
    import goldbot.backtest.engine as engine
    import goldbot.data.feed as feed_mod

    class _EmptyFeed:
        def series(self, tf, bars, prefer_depth=False):
            return None, type("R", (), {"chosen": "", "attempts": []})()

    monkeypatch.setattr(feed_mod, "build_feed", lambda s: _EmptyFeed())
    out = engine.run_backtest(_Settings())
    assert out["bars_tested"] == 0
    assert any("لا بيانات تاريخية" in n for n in out["notes"])


def test_run_backtest_reports_the_primary_frame_and_a_timestamp(monkeypatch):
    import goldbot.backtest.engine as engine
    import goldbot.data.feed as feed_mod

    class _Feed:
        def series(self, tf, bars, prefer_depth=False):
            return Series(timeframe=tf, bars=_bars(400), source="t"), type(
                "R", (), {"chosen": "t", "attempts": []}
            )()

    monkeypatch.setattr(feed_mod, "build_feed", lambda s: _Feed())
    out = engine.run_backtest(_Settings())
    assert any("الفريم الأساسي" in n for n in out["notes"])
    assert any("وقت التشغيل" in n for n in out["notes"])
    assert out["symbol"] == "XAUUSD"
    assert out["bars_tested"] == 400


def test_run_backtest_drops_a_frame_below_the_ninety_bar_floor(monkeypatch):
    import goldbot.backtest.engine as engine
    import goldbot.data.feed as feed_mod

    class _Feed:
        def series(self, tf, bars, prefer_depth=False):
            n = 400 if tf == "H1" else 50  # M15 is too thin to use
            return Series(timeframe=tf, bars=_bars(n), source="t"), type(
                "R", (), {"chosen": "t", "attempts": []}
            )()

    monkeypatch.setattr(feed_mod, "build_feed", lambda s: _Feed())
    out = engine.run_backtest(_Settings())
    assert any("الفريم الأساسي: H1" in n for n in out["notes"])


def test_run_backtest_honours_an_explicit_primary_frame(monkeypatch):
    """``--tf`` must change the walk, not be silently ignored."""
    import goldbot.backtest.engine as engine
    import goldbot.data.feed as feed_mod

    seen = {}

    class _Feed:
        def series(self, tf, bars, prefer_depth=False):
            return Series(timeframe=tf, bars=_bars(400), source="t"), type(
                "R", (), {"chosen": "t", "attempts": []}
            )()

    monkeypatch.setattr(feed_mod, "build_feed", lambda s: _Feed())
    real_walk = engine._walk

    def _spy(series_by_tf, min_rr, **kw):
        seen["primary_tf"] = kw.get("primary_tf")
        return real_walk(series_by_tf, min_rr, **kw)

    monkeypatch.setattr(engine, "_walk", _spy)
    out = engine.run_backtest(_Settings(), primary_tf="H1")
    assert seen["primary_tf"] == "H1"
    assert any("الفريم الأساسي: H1" in n for n in out["notes"])


def test_the_cost_constant_is_deliberately_unflattering():
    assert 0 < COST_FRACTION < 0.001
    assert HORIZON_BARS > 0
