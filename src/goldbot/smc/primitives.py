"""Numeric primitives shared by the SMC engine.

Pure functions over a list of :class:`~goldbot.models.Bar`, oldest first. No
I/O, no clock, no randomness: the same bars always yield the same numbers, so
every downstream read is reproducible and testable.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..models import Bar

ATR_PERIOD = 14


def true_ranges(bars: Sequence[Bar]) -> list[float]:
    """Wilder's true range per bar (the first bar uses its own high-low)."""
    out: list[float] = []
    for i, bar in enumerate(bars):
        if i == 0:
            out.append(bar.high - bar.low)
        else:
            prev = bars[i - 1].close
            out.append(max(bar.high - bar.low, abs(bar.high - prev), abs(bar.low - prev)))
    return out


def atr(bars: Sequence[Bar], period: int = ATR_PERIOD) -> float | None:
    """Average true range over ``period`` bars. ``None`` when there is too little data."""
    if len(bars) < period + 1:
        return None
    trs = true_ranges(bars)[-period:]
    if not trs:
        return None
    return sum(trs) / len(trs)


def swings(
    bars: Sequence[Bar], lookback: int = 2
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal swing highs and lows.

    A bar is a swing high when its high is the strict extreme of ``lookback``
    bars on each side. Returns ``(highs, lows)`` as ``(index, price)`` pairs,
    oldest first. A pivot needs ``lookback`` bars *after* it to confirm, so the
    newest bars can never contribute a pivot — which is what keeps the read
    causal.
    """
    highs: list[tuple[int, float]] = []
    lows: list[tuple[int, float]] = []
    n = len(bars)
    for i in range(lookback, n - lookback):
        window = bars[i - lookback : i + lookback + 1]
        if bars[i].high == max(b.high for b in window) and bars[i].high > max(
            b.high for b in window if b is not bars[i]
        ):
            highs.append((i, bars[i].high))
        if bars[i].low == min(b.low for b in window) and bars[i].low < min(
            b.low for b in window if b is not bars[i]
        ):
            lows.append((i, bars[i].low))
    return highs, lows


def structure_events(
    bars: Sequence[Bar], lookback: int = 2
) -> tuple[str, list[dict], float | None, float | None]:
    """Walk the swings and record every break of structure / change of character.

    Returns ``(trend, events, last_swing_high, last_swing_low)`` where ``trend``
    is ``"bullish"``, ``"bearish"`` or ``"range"``.

    The rules are the textbook ones:

      * A **body close** above the most recent confirmed swing high is a bullish
        BOS when the prior structure was already bullish, and a bullish CHoCH
        when it flips a bearish structure — the character has changed.
      * The mirror holds below a swing low.
      * A wick beyond a level that closes back inside is *not* an event here;
        the engine records those separately as sweeps.
    """
    highs, lows = swings(bars, lookback)
    events: list[dict] = []
    # With no pivots at all there is nothing to break. Requiring *both* sides
    # would be too strict: a clean impulse can show swing highs long before it
    # prints a confirmed swing low, and refusing to read it would hide a real
    # change of character.
    if not highs and not lows:
        return "range", events, None, None

    trend = "range"
    last_high: float | None = None
    last_low: float | None = None
    hi_i = lo_i = 0

    for i, bar in enumerate(bars):
        # Adopt any swing that has confirmed by this bar.
        while hi_i < len(highs) and highs[hi_i][0] < i:
            last_high = highs[hi_i][1]
            hi_i += 1
        while lo_i < len(lows) and lows[lo_i][0] < i:
            last_low = lows[lo_i][1]
            lo_i += 1

        if last_high is not None and bar.close > last_high:
            kind = "BOS" if trend == "bullish" else "CHoCH"
            events.append(
                {
                    "type": kind,
                    "direction": "bullish",
                    "level": last_high,
                    "time": bar.time,
                    "index": i,
                }
            )
            trend = "bullish"
            last_high = None
        elif last_low is not None and bar.close < last_low:
            kind = "BOS" if trend == "bearish" else "CHoCH"
            events.append(
                {
                    "type": kind,
                    "direction": "bearish",
                    "level": last_low,
                    "time": bar.time,
                    "index": i,
                }
            )
            trend = "bearish"
            last_low = None

    return trend, events, last_high, last_low
