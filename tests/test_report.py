"""The metric layer must be correct, and must never dress a gap as a zero.

A backtest report is read by a human deciding whether to trust a strategy, so
these tests pin the two things that make it trustworthy: the arithmetic (a
profit factor, a drawdown, a streak computed by hand and matched), and the
honesty rule — a metric that cannot be computed is ``None`` and prints as an
em dash, never as ``0``.
"""

from __future__ import annotations

from goldbot.backtest.engine import Trade
from goldbot.backtest.report import (
    _longest_loss_streak,
    _max_drawdown,
    _sharpe,
    _sortino,
    consistency,
    equity_curve,
    equity_points,
    format_report,
    metrics,
    windows,
)


def _t(r: float, time: int = 0, direction: str = "buy", setup: str = "s") -> Trade:
    """A resolved trade carrying a chosen R, for arithmetic checks."""
    return Trade(
        index=time,
        time=time,
        direction=direction,
        entry=2000.0,
        stop=1990.0,
        target=2020.0,
        rr=2.0,
        setup=setup,
        outcome="win" if r > 0 else ("loss" if r < 0 else "timeout"),
        r_multiple=r,
    )


def _unfilled() -> Trade:
    return Trade(0, 0, "buy", 2000.0, 1990.0, 2020.0, 2.0, "s", outcome="no_fill")


def test_profit_factor_and_payoff_match_hand_arithmetic():
    # Wins: +2, +2. Losses: -1, -1, -1. PF = 4/3; payoff = 2/1.
    trades = [_t(2.0), _t(2.0), _t(-1.0), _t(-1.0), _t(-1.0)]
    m = metrics(trades)
    assert m["n"] == 5
    assert round(m["profit_factor"], 3) == round(4 / 3, 3)
    assert m["payoff"] == 2.0
    assert m["expectancy"] == 0.2
    assert m["total_r"] == 1.0


def test_unfilled_orders_are_excluded_from_every_metric():
    trades = [_t(2.0), _unfilled(), _t(-1.0), _unfilled()]
    m = metrics(trades)
    assert m["n"] == 2
    assert m["no_fill"] == 2
    assert m["total_r"] == 1.0


def test_a_metric_that_cannot_be_computed_is_none_never_zero():
    m = metrics([])
    # Empty: every derived number is absent, and n alone is a real zero.
    assert m["n"] == 0
    for key in ("win_rate", "expectancy", "profit_factor", "sharpe", "max_drawdown_r"):
        assert m[key] is None, key


def test_profit_factor_is_none_when_there_are_no_losses():
    """No division by zero dressed as a perfect score."""
    m = metrics([_t(2.0), _t(1.0)])
    assert m["profit_factor"] is None
    assert m["avg_loss_r"] is None
    assert m["payoff"] is None


def test_win_rate_counts_a_target_hit_and_not_a_positive_timeout():
    """A win is the target; a timeout that merely closed up is not one.

    The numerator and the denominator both use the target/stop binary, so a
    positive timeout is in neither — the same rule the walk engine applies.
    """
    trades = [
        _t(2.0),  # win  — target
        _t(-1.0),  # loss — stop
        Trade(3, 3, "buy", 2000.0, 1990.0, 2020.0, 2.0, "s", outcome="timeout", r_multiple=0.4),
    ]
    m = metrics(trades)
    assert m["win_rate"] == 0.5  # 1 win of 2 decided, not 2 of 3
    # The timeout still carries money, so it belongs in total R and in PF.
    assert m["total_r"] == 1.4
    assert round(m["profit_factor"], 3) == round(2.4 / 1.0, 3)


def test_metrics_win_rate_agrees_with_the_engine_summary():
    """One name must not mean two things across the two reporting layers."""
    from goldbot.backtest.engine import BacktestReport, _summarise

    trades = [
        _t(2.0),
        _t(-1.0),
        Trade(3, 3, "buy", 2000.0, 1990.0, 2020.0, 2.0, "s", outcome="timeout", r_multiple=0.4),
    ]
    report = _summarise(BacktestReport(symbol="XAUUSD", bars_tested=100, signals=0), trades)
    assert metrics(trades)["win_rate"] == report.win_rate


def test_payoff_is_the_ratio_of_the_win_and_loss_averages():
    # Wins +2, +2 (avg 2); losses -1, -3 (avg -2): payoff = 2 / 2 = 1.
    m = metrics([_t(2.0), _t(2.0), _t(-1.0), _t(-3.0)])
    assert m["avg_win_r"] == 2.0
    assert m["avg_loss_r"] == -2.0
    assert m["payoff"] == 1.0


def test_max_drawdown_is_the_worst_peak_to_trough():
    # +1, +1 (=2), -1, -1, -1 (=-1), +0.5. Peak 2, trough -1 → -3.
    rs = [1.0, 1.0, -1.0, -1.0, -1.0, 0.5]
    assert _max_drawdown(rs) == -3.0


def test_max_drawdown_is_zero_when_the_curve_only_rises():
    assert _max_drawdown([1.0, 2.0, 3.0]) == 0.0


def test_longest_loss_streak_counts_consecutive_losses():
    rs = [1.0, -1.0, -1.0, -1.0, 1.0, -1.0]
    assert _longest_loss_streak(rs) == 3


def test_sharpe_and_sortino_refuse_a_single_trade():
    assert _sharpe([1.0]) is None
    assert _sortino([1.0]) is None


def test_sortino_is_none_without_downside():
    assert _sortino([1.0, 2.0, 3.0]) is None


def test_sharpe_uses_total_deviation_and_sortino_only_the_losses():
    rs = [2.0, -1.0, 3.0, -1.5, 0.5]
    # Both exist; Sortino is computed off the loss side only, so it differs.
    assert _sharpe(rs) is not None
    assert _sortino(rs) is not None
    assert _sharpe(rs) != _sortino(rs)


def test_sortino_is_none_when_every_loss_is_identical():
    """Equal losses give zero downside deviation — absent, not infinite."""
    assert _sortino([2.0, -1.0, -1.0, 3.0]) is None


def test_ar_count_agrees_with_the_counted_noun():
    from goldbot.backtest.report import _ar_count

    words = ("صفقة", "صفقتان", "صفقات")
    assert _ar_count(1, *words) == "1 صفقة"
    assert _ar_count(2, *words) == "2 صفقتان"
    assert _ar_count(4, *words) == "4 صفقات"
    # 11–99 take the singular again, as in the streak of 34 a real run prints.
    assert _ar_count(34, *words) == "34 صفقة"


def test_fmt_signs_returns_but_not_proportions():
    from goldbot.backtest.report import _fmt

    # A return-like figure shows its direction; a rate or a p-value does not.
    assert _fmt(0.055) == "+0.055"
    assert _fmt(-0.042) == "-0.042"
    assert _fmt(29.5, 1, "%", signed=False) == "29.5%"
    assert _fmt(0.287, signed=False) == "0.287"
    assert _fmt(None) == "—"


def test_equity_curve_is_cumulative_and_ignores_unfilled():
    curve = equity_curve([_t(1.0), _unfilled(), _t(-0.5), _t(2.0)])
    assert curve == [1.0, 0.5, 2.5]


def test_equity_points_carry_the_clock_and_sort_by_time():
    """The time-carrying curve is the same running total, ordered oldest first.

    Unlike :func:`equity_curve`, which keeps the caller's order, the points sort
    by time — the axis of a time chart is time, so an out-of-order input must not
    produce a curve that doubles back.
    """
    points = equity_points([_t(-0.5, time=30), _t(2.0, time=10), _unfilled()])
    assert points == [(10, 2.0), (30, 1.5)]
    # Same trades in time order give the same running total, either way round.
    ordered = equity_points([_t(2.0, time=10), _t(-0.5, time=30)])
    assert [v for _, v in ordered] == equity_curve([_t(2.0, time=10), _t(-0.5, time=30)])


def test_windows_split_by_time_not_by_shuffle():
    trades = [_t(1.0, time=i) for i in range(10)]
    blocks = windows(trades, 5)
    assert len(blocks) == 5
    assert [len(b) for b in blocks] == [2, 2, 2, 2, 2]
    # The first block holds the earliest times: contiguous, not interleaved.
    assert max(t.time for t in blocks[0]) < min(t.time for t in blocks[1])


def test_windows_put_the_remainder_on_the_last_block():
    trades = [_t(1.0, time=i) for i in range(11)]
    blocks = windows(trades, 5)
    assert sum(len(b) for b in blocks) == 11
    assert len(blocks[-1]) == 3


def test_windows_degenerate_inputs_do_not_crash():
    assert windows([], 5) == [[]]
    assert windows([_t(1.0)], 1) == [[_t(1.0)]] or len(windows([_t(1.0)], 1)) == 1


def test_consistency_counts_positive_windows_and_starves_thin_ones():
    good = [_t(1.0) for _ in range(10)]
    bad = [_t(-1.0) for _ in range(10)]
    thin = [_t(1.0)]  # below the window floor
    c = consistency([good, bad, thin])
    assert c["windows"] == 2 and c["positive"] == 1
    assert c["share"] == 0.5
    assert c["thin"] == 1


def test_consistency_with_no_judgeable_window_is_none_not_zero():
    c = consistency([[_t(1.0)], [_t(1.0)]])
    assert c["share"] is None and c["windows"] == 0


def _result():
    trades = [_t(1.5, time=i, setup="bos") for i in range(6)] + [
        _t(-1.0, time=10 + i, direction="sell", setup="sweep") for i in range(4)
    ]
    from goldbot.backtest.control import skill_test

    bars = []
    from goldbot.models import Bar

    for i in range(200):
        bars.append(Bar(time=i * 900, open=2000, high=2001, low=1999, close=2000 + i * 0.1))
    return {
        "symbol": "XAUUSD",
        "primary_tf": "H1",
        "bars": 200,
        "span_days": 30,
        "period": ("2024-01-01", "2024-01-31"),
        "trade_period": ("2024-01-05", "2024-01-20"),
        "trade_span_days": 15,
        "windows": 2,
        "metrics": metrics(trades),
        "consistency": consistency(windows(trades, 2)),
        "control": skill_test(trades, bars, n=50, seed=1),
        "by_direction": {"buy": metrics(trades[:6]), "sell": metrics(trades[6:])},
        "by_setup": {"bos": metrics(trades[:6]), "sweep": metrics(trades[6:])},
        "notes": ["اختبار"],
    }


def test_the_report_prints_an_em_dash_for_an_absent_metric():
    result = _result()
    result["metrics"]["payoff"] = None
    text = format_report(result)
    assert "—" in text
    assert "Payoff" in text


def test_the_report_includes_the_control_verdict_and_the_consistency_line():
    text = format_report(_result())
    assert "حزمة السيطرة" in text
    assert "الثبات عبر الزمن" in text
    assert "XAUUSD" in text


def test_the_report_names_the_sample_period():
    text = format_report(_result())
    assert "2024-01-01" in text and "2024-01-31" in text


def test_the_report_says_where_the_trades_are_not_just_where_the_bars_are():
    """A bar span can overstate the sample; the trade window is the honest one."""
    text = format_report(_result())
    assert "نافذة الصفقات الفعلية" in text
    assert "2024-01-05" in text and "2024-01-20" in text
    # 15 of 30 days is half the bar window, and the report must say so.
    assert "50% من نافذة الشموع" in text


def test_a_trade_window_that_covers_the_bars_carries_no_shortfall_warning():
    result = _result()
    result["trade_period"] = ("2024-01-01", "2024-01-31")
    result["trade_span_days"] = 30
    text = format_report(result)
    assert "نافذة الصفقات الفعلية" in text
    assert "فقط" not in text


def test_the_report_explains_the_outcome_split():
    """The win-rate denominator is smaller than n; the split says why."""
    text = format_report(_result())
    assert "ربح" in text and "خسارة" in text and "مهلة" in text


def test_the_report_prints_the_per_setup_verdict():
    result = _result()
    result["setup_control"] = {
        "bos": {
            "verdict": "skill",
            "random_p": 0.001,
            "regime_p": 0.02,
        }
    }
    text = format_report(result)
    assert "الحكم=skill" in text
    assert "نظام p=0.02" in text
    # A p-value is a proportion — it must not be dressed with a plus sign.
    assert "p=+0" not in text


def test_the_report_handles_an_empty_study_without_crashing():
    from goldbot.backtest.study import run_study

    result = {
        "symbol": "XAUUSD",
        "primary_tf": "H1",
        "bars": 0,
        "span_days": 0,
        "windows": 0,
        "metrics": metrics([]),
        "consistency": consistency([]),
        "control": None,
        "by_direction": {},
        "by_setup": {},
        "notes": ["لا بيانات"],
    }
    text = format_report(result)
    assert "لا بيانات" in text
    assert run_study is not None
