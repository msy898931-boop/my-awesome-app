"""Kraken feed — keyless 24/7 gold via the ``PAXG`` token.

Kraken's public OHLC endpoint needs no key and never closes. ``PAXG`` is one
troy ounce of allocated gold, redeemable, and its price tracks spot XAUUSD
closely — with a small premium that is a known property, not noise. This
provider exists to cover the weekend and holiday gap where the futures feed is
dark, so the bot can still describe structure when COMEX is shut.
"""

from __future__ import annotations

from ..models import Bar, Series
from .base import Provider, ProviderError, clean_bars, http_get_json

BASE = "https://api.kraken.com/0/public/OHLC"

# Kraken intervals are in minutes.
INTERVALS = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}


class KrakenProvider(Provider):
    """Gold-backed token OHLC, live around the clock."""

    name = "kraken:PAXGUSD"
    always_on = True

    def __init__(self, pair: str = "PAXGUSD") -> None:
        self.pair = pair

    def fetch(self, timeframe: str, bars: int) -> Series:
        interval = INTERVALS.get(timeframe.upper())
        if interval is None:
            raise ProviderError(f"unsupported timeframe {timeframe!r}")
        url = f"{BASE}?pair={self.pair}&interval={interval}"
        payload = http_get_json(url)

        errors = (payload or {}).get("error") or []
        if errors:
            raise ProviderError(f"kraken: {errors[0]}")
        result = (payload or {}).get("result") or {}
        rows = next((v for k, v in result.items() if k != "last"), None)
        if not rows:
            raise ProviderError("no rows")

        built: list[Bar] = []
        for row in rows:
            # [time, open, high, low, close, vwap, volume, count]
            built.append(
                Bar(
                    time=int(row[0]),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[6] or 0.0),
                )
            )
        series = Series(
            timeframe=timeframe.upper(), bars=clean_bars(built)[-bars:], source=self.name
        )
        if not series.bars:
            raise ProviderError("no usable bars")
        return series

    def spot(self) -> float | None:
        try:
            payload = http_get_json(f"https://api.kraken.com/0/public/Ticker?pair={self.pair}")
            result = (payload or {}).get("result") or {}
            node: dict = next(iter(result.values()), {})
            close = node.get("c") or []
            return float(close[0]) if close else None
        except (ProviderError, TypeError, ValueError, StopIteration):
            return None
