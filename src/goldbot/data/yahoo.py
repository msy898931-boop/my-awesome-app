"""Yahoo Finance chart feed — keyless COMEX gold futures (``GC=F``).

Yahoo's ``v8/finance/chart`` endpoint needs no key and returns true OHLC with a
Unix open time per bar. ``GC=F`` is the COMEX front-month gold future: it is the
deepest free series for gold, trades nearly 24 hours a day Sunday night through
Friday, and tracks spot XAUUSD within the futures basis. It is the primary
source; the crypto-gold providers fill the weekend gap where it is closed.
"""

from __future__ import annotations

import time
from typing import Any, Optional

from ..models import Bar, Series
from .base import Provider, ProviderError, clean_bars, http_get_json

BASE = "https://query1.finance.yahoo.com/v8/finance/chart"

# Yahoo speaks its own interval vocabulary; the engine speaks ours.
INTERVALS = {
    "M1": "1m",
    "M5": "5m",
    "M15": "15m",
    "M30": "30m",
    "H1": "60m",
    "H4": "1h",  # resampled below; Yahoo has no native 4h
    "D1": "1d",
}

# Yahoo caps intraday history; the window has to be wide enough to hold the
# requested bars at that interval.
RANGES = {
    "1m": "5d",
    "5m": "1mo",
    "15m": "1mo",
    "30m": "1mo",
    "60m": "3mo",
    "1h": "3mo",
    "1d": "1y",
}

# Explicit epoch windows beat the ``range`` shorthand: ``range=1mo`` on 15m
# returns ~720 bars, while ``period1``/``period2`` over the same 60 days returns
# the full ~4800. Yahoo's intraday ceiling is 60 days for sub-hour intervals and
# roughly two years for hourly, so these are the widest honest windows.
MAX_DAYS = {"1m": 7, "5m": 60, "15m": 60, "30m": 60, "60m": 730, "1h": 730, "1d": 3650}


class YahooProvider(Provider):
    """COMEX gold futures via Yahoo's keyless chart endpoint."""

    name = "yahoo:GC=F"
    always_on = False

    def __init__(self, symbol: str = "GC=F") -> None:
        self.symbol = symbol

    def fetch(self, timeframe: str, bars: int) -> Series:
        interval = INTERVALS.get(timeframe.upper())
        if interval is None:
            raise ProviderError(f"unsupported timeframe {timeframe!r}")
        # Prefer an explicit epoch window over the ``range`` shorthand: Yahoo
        # returns far more bars for the same span when asked this way.
        days = MAX_DAYS.get(interval)
        if days:
            now = int(time.time())
            url = (
                f"{BASE}/{self.symbol}?interval={interval}"
                f"&period1={now - days * 86400}&period2={now}&includePrePost=false"
            )
        else:
            url = f"{BASE}/{self.symbol}?interval={interval}&range={RANGES[interval]}&includePrePost=false"
        payload = http_get_json(url)

        result = ((payload or {}).get("chart") or {}).get("result")
        if not result:
            err = ((payload or {}).get("chart") or {}).get("error") or {}
            raise ProviderError(f"no data: {err.get('description', 'empty result')}")

        node = result[0]
        stamps = node.get("timestamp") or []
        quote = ((node.get("indicators") or {}).get("quote") or [{}])[0]
        opens, highs = quote.get("open") or [], quote.get("high") or []
        lows, closes = quote.get("low") or [], quote.get("close") or []
        volumes = quote.get("volume") or []

        built: list[Bar] = []
        for i, ts in enumerate(stamps):
            if i >= len(opens) or opens[i] is None or closes[i] is None:
                continue
            built.append(
                Bar(
                    time=int(ts),
                    open=float(opens[i]),
                    high=float(highs[i] if highs[i] is not None else opens[i]),
                    low=float(lows[i] if lows[i] is not None else opens[i]),
                    close=float(closes[i]),
                    volume=float(volumes[i] or 0.0) if i < len(volumes) else 0.0,
                )
            )

        series = Series(timeframe=timeframe.upper(), bars=clean_bars(built), source=self.name)
        if timeframe.upper() == "H4":
            series = _resample(series, 4)
        series.bars = series.bars[-bars:]
        if not series.bars:
            raise ProviderError("no usable bars")
        return series

    def spot(self) -> Optional[float]:
        try:
            payload = http_get_json(f"{BASE}/{self.symbol}?interval=1m&range=1d")
            node = ((payload or {}).get("chart") or {}).get("result", [{}])[0]
            price = ((node.get("meta") or {}).get("regularMarketPrice"))
            return float(price) if price is not None else None
        except (ProviderError, TypeError, ValueError):
            return None


def _resample(series: Series, factor: int) -> Series:
    """Aggregate consecutive bars ``factor`` at a time, aligned to the epoch.

    Used only to build H4 from Yahoo's H1, which the API does not serve
    natively. The last, possibly partial, bucket is dropped so no half-formed
    candle can masquerade as closed.
    """
    buckets: dict[int, list[Bar]] = {}
    for bar in series.bars:
        key = bar.time - (bar.time % (factor * 3600))
        buckets.setdefault(key, []).append(bar)
    keys = sorted(buckets)
    if keys and len(buckets[keys[-1]]) < factor:
        keys = keys[:-1]
    out: list[Bar] = []
    for key in keys:
        group = buckets[key]
        out.append(
            Bar(
                time=key,
                open=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                volume=sum(b.volume for b in group),
            )
        )
    return Series(timeframe=series.timeframe, bars=out, source=series.source)
