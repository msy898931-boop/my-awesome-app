"""The backtest engine: walk-forward, cost-aware, no look-ahead.

How a trade is judged, precisely:

  * Entry is assumed filled at the plan's entry price on the bar *after* the
    signal. The signal bar is closed; acting on it would be look-ahead.
  * From there, each subsequent bar is checked. If the stop is touched first
    the trade is a loss; if the target is touched first it is a win. When a
    single bar touches both, the stop is assumed hit first — the pessimistic
    reading, because OHLC order within a bar is unknown.
  * If neither is touched within the horizon the trade is a timeout, scored at
    the mark-to-market R rather than discarded.

Costs are charged per trade as a fixed fraction of price (spread + slippage),
so the reported win rate and expectancy are net, not gross.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from ..config import Settings
from ..models import Bar
from ..smc.engine import build as build_facts
from ..strategy.plan import build_plan

# How many bars a trade is given to resolve before it times out.
HORIZON_BARS = 60
# How long a limit order rests before it is cancelled unfilled. An SMC entry is
# usually a limit at a zone, not a market order: if price never returns to the
# zone the trade simply never happens, and counting it would invent losses.
FILL_WINDOW_BARS = 12
# Round-trip cost as a fraction of price: spread plus slippage. Gold spread on
# a retail account runs a few cents to a dollar; this is a deliberately
# unflattering constant so the result cannot be flattered by it.
COST_FRACTION = 0.0002


@dataclass
class Trade:
    """One evaluated signal."""

    index: int
    time: int
    direction: str
    entry: float
    stop: float
    target: float
    rr: float
    setup: str
    timeframe: str = ""
    outcome: str = "open"  # win | loss | timeout
    r_multiple: float = 0.0
    bars_held: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestReport:
    """The honest scoreboard."""

    symbol: str
    bars_tested: int
    signals: int
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    no_fill: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    max_drawdown_r: float = 0.0
    profit_factor: float = 0.0
    by_setup: dict = field(default_factory=dict)
    by_direction: dict = field(default_factory=dict)
    trades: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    cost_fraction: float = COST_FRACTION
    horizon_bars: int = HORIZON_BARS

    def as_dict(self) -> dict:
        return asdict(self)


def evaluate_trade(
    trade: Trade, future: list[Bar], cost_fraction: float = COST_FRACTION,
    fill_window: int = FILL_WINDOW_BARS,
) -> Trade:
    """Resolve a trade against the bars that followed it, in two phases.

    **Phase 1 — fill.** The plan's entry is treated as a resting limit order.
    Only a bar whose range actually reaches the entry fills it; if none does
    within ``fill_window`` bars the signal is void and marked ``no_fill``. This
    matters: an SMC entry is a return to a zone, and assuming an instant fill
    at a price the market never traded invents trades that never existed.

    **Phase 2 — resolution.** From the fill bar onward, the stop is checked
    before the target within each bar. With only OHLC the intrabar order is
    unknown, and assuming the stop is the pessimistic, honest choice.
    """
    risk = abs(trade.entry - trade.stop)
    if risk <= 0 or not future:
        trade.outcome = "no_fill"
        return trade

    fill_at = None
    for i, bar in enumerate(future[:fill_window]):
        if bar.low <= trade.entry <= bar.high:
            fill_at = i
            break
    if fill_at is None:
        trade.outcome = "no_fill"
        return trade

    trade.bars_held = 1
    cost = trade.entry * cost_fraction
    remaining = future[fill_at + 1 : fill_at + 1 + HORIZON_BARS]

    for i, bar in enumerate(remaining):
        trade.bars_held = i + 2
        if trade.direction == "buy":
            if bar.low <= trade.stop:
                trade.outcome = "loss"
                trade.r_multiple = round(-1.0 - cost / risk, 3)
                return trade
            if bar.high >= trade.target:
                trade.outcome = "win"
                trade.r_multiple = round(trade.rr - cost / risk, 3)
                return trade
        else:
            if bar.high >= trade.stop:
                trade.outcome = "loss"
                trade.r_multiple = round(-1.0 - cost / risk, 3)
                return trade
            if bar.low <= trade.target:
                trade.outcome = "win"
                trade.r_multiple = round(trade.rr - cost / risk, 3)
                return trade

    if not remaining:
        trade.outcome = "timeout"
        trade.r_multiple = 0.0
        return trade

    # Timed out: score at the mark, net of cost.
    last = remaining[-1]
    move = (last.close - trade.entry) if trade.direction == "buy" else (trade.entry - last.close)
    trade.outcome = "timeout"
    trade.r_multiple = round((move - cost) / risk, 3)
    return trade


def _walk(
    series_by_tf: dict,
    min_rr: float,
    warmup: int = 60,
    step: int = 5,
    primary_tf: str = "M15",
) -> list[Trade]:
    """Walk forward across the primary frame, signalling at each step.

    Signals are generated on a schedule rather than every bar, both to keep the
    run quick and because re-deciding every five minutes is not how the bot
    behaves. ``warmup`` bars are withheld at the front so structure detection
    has history to read.

    ``primary_tf`` selects the frame that is walked. M15 gives the most
    signals but the shortest calendar window, because every free source caps
    near 720 bars; a higher frame trades signal count for a longer window and
    is the honest way to test whether a result survives more than a week.
    """
    if primary_tf not in series_by_tf:
        primary_tf = "M15" if "M15" in series_by_tf else next(iter(series_by_tf))
    primary = series_by_tf[primary_tf].bars
    if len(primary) < warmup + HORIZON_BARS + 5:
        return []

    trades: list[Trade] = []
    for i in range(warmup, len(primary) - HORIZON_BARS - 1, step):
        sliced = _slice_at(series_by_tf, primary_tf, i)
        sheet = build_facts(sliced, "XAUUSD", now=primary[i].time)
        plan = build_plan(sheet.as_dict(), min_rr)
        if not plan.is_actionable:
            continue
        trade = Trade(
            index=i,
            time=primary[i].time,
            direction=plan.direction,
            entry=plan.entry,
            stop=plan.stop,
            target=plan.target,
            rr=plan.rr,
            setup=plan.setup,
            timeframe=(plan.levels or {}).get("timeframe", "") or "",
        )
        trades.append(evaluate_trade(trade, primary[i + 1 : i + 1 + HORIZON_BARS]))
    return trades


def _slice_at(series_by_tf: dict, primary_tf: str, index: int, bars: int = 220) -> dict:
    """Series truncated to ``index`` on the primary frame, then windowed.

    Higher frames are cut by time so every frame sees only bars that had
    closed at the moment of the signal — the property that makes the walk
    forward and not just a walk.

    Each frame is then trimmed to its most recent ``bars``, exactly as the
    live pipeline does. Without this cap the higher frames carry their entire
    fetched history: a 15-minute walk would compare an H4 trend measured over
    113 days against an M15 trend measured over 15 hours, and the mismatch
    alone manufactures signals that could never fire live. The backtest has
    to read the frames the way the bot reads them or it is measuring a
    different system.
    """
    cutoff = series_by_tf[primary_tf].bars[index].time
    out = {}
    for tf, series in series_by_tf.items():
        if tf == primary_tf:
            sliced = series.bars[: index + 1][-bars:]
        else:
            sliced = [b for b in series.bars if b.time <= cutoff][-bars:]
        if len(sliced) < 30:
            continue
        out[tf] = type(series)(timeframe=tf, source=series.source, bars=sliced)
    return out


def _summarise(report: BacktestReport, trades: list[Trade]) -> BacktestReport:
    report.trades = [t.as_dict() for t in trades]
    report.signals = len(trades)
    if not trades:
        report.notes.append("لا إشارات في الفترة المختبرة")
        return report

    # Unfilled limit orders are not trades. They are reported, but excluded
    # from the win rate and the R curve, because counting them would mix
    # "the market never came to us" with "we were wrong".
    filled = [t for t in trades if t.outcome != "no_fill"]
    report.no_fill = len(trades) - len(filled)
    if not filled:
        report.notes.append("كل الإشارات لم تُنفَّذ (السعر لم يعد لمنطقة الدخول)")
        return report

    wins = [t for t in filled if t.outcome == "win"]
    losses = [t for t in filled if t.outcome == "loss"]
    timeouts = [t for t in filled if t.outcome == "timeout"]
    report.wins, report.losses, report.timeouts = len(wins), len(losses), len(timeouts)

    decided = len(wins) + len(losses)
    report.win_rate = round(len(wins) / decided, 3) if decided else 0.0

    rs = [t.r_multiple for t in filled]
    report.total_r = round(sum(rs), 2)
    report.avg_r = round(sum(rs) / len(rs), 3)

    gross_win = sum(t.r_multiple for t in wins)
    gross_loss = abs(sum(t.r_multiple for t in losses))
    report.profit_factor = round(gross_win / gross_loss, 2) if gross_loss else 0.0

    # Max drawdown on the cumulative R curve.
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    report.max_drawdown_r = round(worst, 2)

    for group_key, attr in (("by_setup", "setup"), ("by_direction", "direction")):
        buckets: dict[str, list[Trade]] = {}
        for t in filled:
            buckets.setdefault(getattr(t, attr), []).append(t)
        summary = {}
        for name, group in buckets.items():
            w = sum(1 for t in group if t.outcome == "win")
            d = sum(1 for t in group if t.outcome in {"win", "loss"})
            summary[name] = {
                "n": len(group),
                "win_rate": round(w / d, 3) if d else 0.0,
                "avg_r": round(sum(t.r_multiple for t in group) / len(group), 3),
            }
        setattr(report, group_key, summary)

    report.notes.append(
        f"التكلفة المفروضة لكل صفقة: {COST_FRACTION * 100:.3f}% من السعر "
        f"(سبريد + انزلاق)، والأفق {HORIZON_BARS} شمعة"
    )
    if report.no_fill:
        report.notes.append(f"استُثنيت {report.no_fill} إشارة لم تُنفَّذ من حساب نسبة النجاح")
    return report


def run_backtest(settings: Settings, with_ai: bool = False) -> dict:
    """Fetch history, walk it forward, and report the honest scoreboard.

    The AI layer is off by default here: it is non-deterministic, and a study
    that cannot be reproduced is not a study.
    """
    from ..data.feed import build_feed

    feed = build_feed(settings)
    series_by_tf = {}
    for tf in settings.timeframes:
        # Ask for far more than any single free request returns: the paging
        # providers (Binance) serve years, and the archive may hold more still.
        # prefer_depth because a study wants the longest sample, not the
        # freshest tick.
        series, _ = feed.series(tf, 20000, prefer_depth=True)
        if series is not None and len(series) >= 90:
            series_by_tf[tf] = series
    if not series_by_tf:
        return BacktestReport(symbol=settings.symbol, bars_tested=0, signals=0,
                              notes=["لا بيانات تاريخية متاحة"]).as_dict()

    primary_tf = "M15" if "M15" in series_by_tf else next(iter(series_by_tf))
    report = BacktestReport(
        symbol=settings.symbol,
        bars_tested=len(series_by_tf[primary_tf].bars),
        signals=0,
    )
    trades = _walk(series_by_tf, settings.min_rr)
    _summarise(report, trades)
    report.notes.append(f"الفريم الأساسي: {primary_tf}")
    report.notes.append(f"وقت التشغيل: {int(time.time())}")
    return report.as_dict()
