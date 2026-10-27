"""The fact sheet: everything SMC/ICT can read off closed bars, computed.

This module turns a set of series (one per timeframe) into a single JSON-ready
document: per-frame structure, liquidity, imbalances and order blocks, plus a
cross-frame confluence tally and a session map. It states readings, never a
verdict — no direction is asserted, no trade is proposed. The strategy layer
reads this; the AI layer narrates it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional, Sequence

from ..models import Bar, Series
from .primitives import ATR_PERIOD, atr, structure_events, swings

# Two swings within this many ATRs are "equal" — a resting pool of stops.
EQUAL_TOL_ATR = 0.15
# A fair-value gap smaller than this fraction of ATR is noise, not imbalance.
FVG_MIN_ATR = 0.05
# How many bars back from a break the order block that fed it may sit.
OB_SCAN_BARS = 4
# How far back a sweep is looked for, in bars of the frame being read.
SWEEP_LOOKBACK_BARS = 80
# Fixed UTC session windows (start inclusive, end exclusive). Gold trades nearly
# 24h, so these are stated conventions, not a broker's session table.
SESSIONS = (("asian", 0, 7), ("london", 7, 16), ("newyork", 12, 21))
# How much each frame counts toward the top-down bias. The weights halve as the
# frame shortens, so the verdict follows the frame that actually sets the trend
# rather than whichever timeframe happens to be loudest in the last hour.
FRAME_WEIGHTS = {"H4": 8.0, "H1": 4.0, "M30": 2.0, "M15": 1.0, "M5": 0.5}


@dataclass
class FactSheet:
    """The complete deterministic read, ready to serialise."""

    symbol: str
    generated_at: int
    timeframes: dict[str, dict] = field(default_factory=dict)
    confluence: dict = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ── per-frame facts ──────────────────────────────────────────────────────

def _premium_discount(bars: Sequence[Bar]) -> Optional[dict]:
    """Where price sits in the active leg: premium, equilibrium or discount.

    The leg is the range from the lowest low to the highest high over the
    window. Above the 50% midpoint is premium (where sells are favoured),
    below is discount (where buys are favoured) — the ICT dealing range.
    """
    if len(bars) < 10:
        return None
    high = max(b.high for b in bars)
    low = min(b.low for b in bars)
    if high <= low:
        return None
    close = bars[-1].close
    pos = (close - low) / (high - low)
    zone = "premium" if pos > 0.62 else "discount" if pos < 0.38 else "equilibrium"
    return {
        "range_high": round(high, 2),
        "range_low": round(low, 2),
        "equilibrium": round((high + low) / 2, 2),
        "position": round(pos, 3),
        "zone": zone,
    }


def _clusters(points: Sequence[tuple[int, float]], tol: float) -> list[dict]:
    """Group near-equal swing prices into a single liquidity pool."""
    out: list[dict] = []
    for idx, price in points:
        for cluster in out:
            if abs(cluster["price"] - price) <= tol:
                cluster["price"] = (cluster["price"] * cluster["count"] + price) / (
                    cluster["count"] + 1
                )
                cluster["count"] += 1
                cluster["last_index"] = max(cluster["last_index"], idx)
                break
        else:
            out.append({"price": price, "count": 1, "last_index": idx})
    return out


def _liquidity(bars: Sequence[Bar], atr_value: Optional[float]) -> dict:
    """Resting stop pools: equal highs (buy-side) and equal lows (sell-side)."""
    highs, lows = swings(bars)
    tol = (atr_value or 1.0) * EQUAL_TOL_ATR
    eqh = [c for c in _clusters(highs, tol) if c["count"] >= 2]
    eql = [c for c in _clusters(lows, tol) if c["count"] >= 2]
    for c in eqh + eql:
        c["price"] = round(c["price"], 2)
    return {
        "equal_highs": sorted(eqh, key=lambda c: c["last_index"], reverse=True)[:5],
        "equal_lows": sorted(eql, key=lambda c: c["last_index"], reverse=True)[:5],
    }


def _sweeps(bars: Sequence[Bar]) -> list[dict]:
    """Stop-hunts: a wick beyond a prior swing that closes back inside.

    Detected separately from breaks precisely because a sweep is *not* a break.
    The bar takes the level, fails to hold it, and closes on the other side.
    """
    if len(bars) < 5:
        return []
    window = bars[-SWEEP_LOOKBACK_BARS:]
    highs, lows = swings(window)
    out: list[dict] = []
    for i, bar in enumerate(window):
        for idx, price in highs:
            if idx < i and bar.high > price and bar.close < price:
                out.append(
                    {"side": "sell_side_taken", "level": round(price, 2),
                     "time": bar.time, "index": i}
                )
                break
        for idx, price in lows:
            if idx < i and bar.low < price and bar.close > price:
                out.append(
                    {"side": "buy_side_taken", "level": round(price, 2),
                     "time": bar.time, "index": i}
                )
                break
    return out[-6:]


def _fair_value_gaps(bars: Sequence[Bar], atr_value: Optional[float]) -> list[dict]:
    """Three-bar imbalances: a gap between bar i-2's wick and bar i's wick.

    A bullish FVG is where bar i's low sits above bar i-2's high; the untraded
    span between them is an inefficiency price often returns to. The mirror is
    bearish. Sub-noise gaps are dropped.
    """
    if len(bars) < 3:
        return []
    floor = (atr_value or 0.0) * FVG_MIN_ATR
    out: list[dict] = []
    for i in range(2, len(bars)):
        a, c = bars[i - 2], bars[i]
        if c.low > a.high and (c.low - a.high) >= floor:
            out.append(
                {"side": "bullish", "top": round(c.low, 2), "bottom": round(a.high, 2),
                 "time": bars[i - 1].time, "index": i, "filled": bars[-1].low <= a.high}
            )
        elif c.high < a.low and (a.low - c.high) >= floor:
            out.append(
                {"side": "bearish", "top": round(a.low, 2), "bottom": round(c.high, 2),
                 "time": bars[i - 1].time, "index": i, "filled": bars[-1].high >= a.low}
            )
    # Only the unfilled, most recent gaps matter to a live read.
    return [g for g in out if not g["filled"]][-5:]


def _order_blocks(bars: Sequence[Bar], events: Sequence[dict]) -> list[dict]:
    """The last opposing candle before a structural break — institutional supply/demand.

    For a bullish break, the order block is the last down-candle within the
    scan window before the break; for a bearish break, the last up-candle. Its
    body and wick bound the zone institutions are said to have filled.
    """
    out: list[dict] = []
    for ev in events[-6:]:
        idx = ev["index"]
        lo = max(0, idx - OB_SCAN_BARS)
        block: Optional[Bar] = None
        for j in range(idx - 1, lo - 1, -1):
            bar = bars[j]
            if ev["direction"] == "bullish" and bar.close < bar.open:
                block = bar
                break
            if ev["direction"] == "bearish" and bar.close > bar.open:
                block = bar
                break
        if block is None:
            continue
        out.append(
            {
                "side": ev["direction"],
                "top": round(max(block.open, block.close), 2),
                "bottom": round(min(block.open, block.close), 2),
                "wick": round(block.low if ev["direction"] == "bullish" else block.high, 2),
                "time": block.time,
                "from_event": ev["type"],
                "mitigated": bars[-1].low <= min(block.open, block.close)
                if ev["direction"] == "bullish"
                else bars[-1].high >= max(block.open, block.close),
            }
        )
    return out


def _session_ranges(bars: Sequence[Bar]) -> dict:
    """High/low of each fixed UTC session over the bars provided.

    Bars carry their open time in Unix seconds; ``time`` is treated as UTC.
    """
    from datetime import datetime, timezone

    buckets: dict[str, list[Bar]] = {name: [] for name, _, _ in SESSIONS}
    for bar in bars:
        hour = datetime.fromtimestamp(bar.time, tz=timezone.utc).hour
        for name, start, end in SESSIONS:
            if start <= hour < end:
                buckets[name].append(bar)
    out: dict[str, dict] = {}
    for name, group in buckets.items():
        if group:
            out[name] = {
                "high": round(max(b.high for b in group), 2),
                "low": round(min(b.low for b in group), 2),
                "bars": len(group),
            }
    return out


def timeframe_facts(series: Series) -> Optional[dict]:
    """Everything the engine can read off one timeframe, or ``None`` if too thin."""
    bars = series.bars
    if len(bars) < 30:
        return None
    atr_value = atr(bars, ATR_PERIOD)
    trend, events, last_high, last_low = structure_events(bars)
    last_event = events[-1] if events else None
    return {
        "timeframe": series.timeframe,
        "bars": len(bars),
        "last_close": round(bars[-1].close, 2),
        "last_time": bars[-1].time,
        "atr": round(atr_value, 2) if atr_value else None,
        "trend": trend,
        "last_event": last_event,
        "event_count": len(events),
        "recent_events": events[-4:],
        "last_swing_high": round(last_high, 2) if last_high else None,
        "last_swing_low": round(last_low, 2) if last_low else None,
        "premium_discount": _premium_discount(bars),
        "liquidity": _liquidity(bars, atr_value),
        "sweeps": _sweeps(bars),
        "fair_value_gaps": _fair_value_gaps(bars, atr_value),
        "order_blocks": _order_blocks(bars, events),
        "sessions": _session_ranges(bars),
    }


def _confluence(frames: dict[str, dict]) -> dict:
    """Tally how the frames agree, weighting the higher ones more.

    The bias is meant to be the top-down verdict — what the market is doing,
    not what the last few minutes did. An unweighted count makes those the
    same thing, so a choppy hour on M5 can outvote a clean H4 downtrend and
    the plan builder then buys into a falling market. That is precisely how
    the buy side produced a single-digit win rate.

    The weights halve as you step down, which mirrors how the frames nest:
    one H4 bar contains four H1 bars, four M30 bars, and so on.
    """
    bull_w = bear_w = range_w = 0.0
    bullish = bearish = ranging = 0
    for tf, f in frames.items():
        weight = FRAME_WEIGHTS.get(tf, 1.0)
        trend = f.get("trend")
        if trend == "bullish":
            bullish += 1
            bull_w += weight
        elif trend == "bearish":
            bearish += 1
            bear_w += weight
        elif trend == "range":
            ranging += 1
            range_w += weight

    total = bull_w + bear_w + range_w or 1.0
    if bull_w > bear_w:
        bias, strength = "bullish", bull_w / total
    elif bear_w > bull_w:
        bias, strength = "bearish", bear_w / total
    else:
        bias, strength = "neutral", 0.0
    return {
        "bullish_frames": bullish,
        "bearish_frames": bearish,
        "ranging_frames": ranging,
        "frames_read": len(frames),
        "bias": bias,
        "alignment": round(strength, 2),
        "weighted": {"bullish": round(bull_w, 2), "bearish": round(bear_w, 2)},
    }


def build(series_by_tf: dict[str, Series], symbol: str = "XAUUSD", now: Optional[int] = None) -> FactSheet:
    """Assemble the fact sheet from one series per timeframe.

    ``now`` is injectable so a build is reproducible in tests. Frames too thin
    to read are omitted rather than guessed at, and the omission is noted.
    """
    import time as _time

    sheet = FactSheet(symbol=symbol, generated_at=int(now if now is not None else _time.time()))
    for tf, series in series_by_tf.items():
        facts = timeframe_facts(series)
        if facts is None:
            sheet.notes.append(f"{tf}: too few bars to read structure")
            continue
        sheet.timeframes[tf] = facts
        sheet.sources[tf] = series.source
    sheet.confluence = _confluence(sheet.timeframes)
    if not sheet.timeframes:
        sheet.notes.append("no timeframe had enough bars to read")
    return sheet
