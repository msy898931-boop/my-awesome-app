"""The professional study: a deep, walk-forward, out-of-sample backtest.

This is the runner that turns the pieces into a verdict a person can act on:

1. **Deep data.** It reads the longest series each frame can offer — the paging
   Binance feed reaches years, so the sample is long enough to judge rather than
   a week that cannot tell an edge from luck. The archive is preferred when it
   is longer, and the source actually used is named in the report.
2. **Walk-forward, in windows.** The trades are split into contiguous blocks
   by time, and each is scored on its own. The strategy has no fitted
   parameters — nothing is trained on a window and tested on the next — so
   these windows are not out-of-sample in the fitting sense. They do something
   the total cannot: a positive average carried by one stretch of months is a
   regime, not an edge, and the consistency figure is what exposes it. The
   control, not the window split, is what separates skill from drift.
3. **The matched control.** The whole sample is put next to the surrogates from
   :mod:`goldbot.backtest.control` — same geometry, random entry time, and a
   regime-matched draw — so the result is separated into skill and drift instead
   of asserted as one number.
4. **A printable report.** :func:`goldbot.backtest.report.format_report` lays it
   out with an em dash wherever a value could not be computed, so a thin sample
   never reads as a zero.

Nothing here touches the network twice for the same answer: the series are
fetched once, and every window is cut from that copy, so two runs over the same
archive agree. The AI layer is absent by design — a study that cannot be
reproduced is not a study.
"""

from __future__ import annotations

import time

from ..config import Settings
from .control import CONTROL, skill_test
from .engine import _walk
from .report import (
    consistency,
    equity_curve,
    equity_points,
    format_report,
    metrics,
    windows,
)

# Preference order for the walked frame, deepest sample first. Every frame the
# bot actually reads is still loaded — the walk reads them the way live does —
# but H1/H4 give the longest calendar window, so they lead.
PRIMARY_PREFERENCE = ("H1", "H4", "M30", "M15", "M5")


def _load_series(settings: Settings, frames: tuple[str, ...], want: int) -> dict:
    """Fetch the longest available series per frame, preferring the archive.

    The frames are the ones the bot reads live, so the facts built during the
    walk are built from the same inputs — a backtest that reads a different set
    is measuring a different system.
    """
    from ..data.feed import build_feed

    feed = build_feed(settings)
    series_by_tf = {}
    for tf in frames:
        series, _ = feed.series(tf, want, prefer_depth=True)
        if series is not None and len(series.bars) >= 90:
            series_by_tf[tf] = series
    return series_by_tf


def _pick_primary(series_by_tf: dict, requested: str | None) -> str:
    if requested and requested in series_by_tf:
        return requested
    for tf in PRIMARY_PREFERENCE:
        if tf in series_by_tf:
            return tf
    return max(series_by_tf, key=lambda tf: len(series_by_tf[tf].bars))


def run_study(
    settings: Settings,
    primary_tf: str | None = None,
    window_count: int = 5,
    n_control: int = CONTROL,
    want: int = 20000,
) -> dict:
    """Run the deep study and return a structured, printable result.

    ``window_count`` contiguous out-of-sample windows are scored, the whole
    sample is measured against the control, and the two are combined into one
    dict — the shape :func:`report.format_report` expects.
    """
    series_by_tf = _load_series(settings, settings.timeframes, want)
    if not series_by_tf:
        return {
            "symbol": settings.symbol,
            "primary_tf": primary_tf or "?",
            "bars": 0,
            "span_days": 0,
            "period": None,
            "trade_period": None,
            "trade_span_days": 0,
            "windows": 0,
            "metrics": metrics([]),
            "equity_curve": [],
            "equity_points": [],
            "setup_curves": {},
            "setup_points": {},
            "consistency": {"windows": 0, "positive": 0, "share": None, "thin": 0},
            "control": None,
            "by_direction": {},
            "by_setup": {},
            "notes": ["لا بيانات تاريخية متاحة"],
        }

    primary_tf = _pick_primary(series_by_tf, primary_tf)
    primary_series = series_by_tf[primary_tf]
    primary = primary_series.bars
    trades = _walk(series_by_tf, settings.min_rr, primary_tf=primary_tf)

    blocks = windows(trades, window_count)
    block_metrics = [metrics(b) for b in blocks]

    by_direction = {d: metrics([t for t in trades if t.direction == d]) for d in ("buy", "sell")}
    by_direction = {k: v for k, v in by_direction.items() if v["n"]}
    setups = sorted({t.setup for t in trades})
    by_setup = {s: metrics([t for t in trades if t.setup == s]) for s in setups}
    # One curve per setup, not just the pooled one: the headline curve averages
    # three different bets, so a setup that climbs while another bleeds is
    # invisible in it. The pooled curve is the sum of these, and seeing them
    # apart is the whole reason the per-setup verdict exists.
    setup_curves = {s: equity_curve([t for t in trades if t.setup == s]) for s in setups}
    # The same curves with their clock, so a chart can use a date axis.
    setup_points = {s: equity_points([t for t in trades if t.setup == s]) for s in setups}

    span_days = round((primary[-1].time - primary[0].time) / 86400) if len(primary) > 1 else 0
    # The calendar the sample actually covers. "833 days" says nothing about
    # *which* 833 — and a strategy's result is only meaningful against the
    # regime it was measured in, so the dates travel with the number.
    period = (
        (
            time.strftime("%Y-%m-%d", time.gmtime(primary[0].time)),
            time.strftime("%Y-%m-%d", time.gmtime(primary[-1].time)),
        )
        if len(primary) > 1
        else None
    )
    # Where the *trades* fall, not just where the bars do. The bars can start a
    # year before the first fill — a warmup, a quiet regime, or a gate that only
    # opens late — and quoting the bar span as if it were the sample would
    # overstate the evidence. The trade span is the honest denominator.
    trade_period = None
    trade_span_days = 0
    filled = [t for t in trades if t.outcome != "no_fill"]
    if filled:
        first, last = min(t.time for t in filled), max(t.time for t in filled)
        trade_span_days = round((last - first) / 86400)
        trade_period = (
            time.strftime("%Y-%m-%d", time.gmtime(first)),
            time.strftime("%Y-%m-%d", time.gmtime(last)),
        )
    verdict = skill_test(trades, primary, n=n_control)

    # The headline verdict is an average over every setup, so a single real edge
    # can be hidden inside it — and a single drifter can drag it down. Score
    # each setup against its own matched control: that is the only way to see
    # whether a cell like BOS continuation is timing or another long-gold bet.
    setup_control = {
        s: skill_test([t for t in trades if t.setup == s], primary, n=n_control)
        for s in sorted({t.setup for t in trades})
    }

    return {
        "symbol": settings.symbol,
        "primary_tf": primary_tf,
        "bars": len(primary),
        "span_days": span_days,
        "period": period,
        "trade_period": trade_period,
        "trade_span_days": trade_span_days,
        "windows": window_count,
        "metrics": metrics(trades),
        "equity_curve": equity_curve(trades),
        "equity_points": equity_points(trades),
        "setup_curves": setup_curves,
        "setup_points": setup_points,
        "window_metrics": block_metrics,
        "consistency": consistency(blocks),
        "control": verdict,
        "setup_control": setup_control,
        "by_direction": by_direction,
        "by_setup": by_setup,
        "source": primary_series.source,
        "notes": [
            f"المصدر المُستخدَم للفريم الأساسي: {primary_series.source or 'غير مُسمّى'}",
            f"نموذج الأسعار: {settings.symbol} عبر مصدر مُتاحة/مؤشر (قد يحمل بريميوم صغير)",
            f"الفريم الأساسي: {primary_tf} عبر {len(series_by_tf)} فريم",
        ],
    }


def print_study(settings: Settings, primary_tf: str | None = None, window_count: int = 5) -> str:
    """Convenience for the CLI: run and return the formatted report as text."""
    return format_report(run_study(settings, primary_tf=primary_tf, window_count=window_count))
