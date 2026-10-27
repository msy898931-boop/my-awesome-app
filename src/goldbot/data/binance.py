"""Binance feed — keyless gold via the ``PAXGUSDT`` pair, with deep history.

Every other free source here caps out near 720 bars: Kraken ignores its own
``since`` parameter, and Yahoo answers 429 from most hosts. That ceiling is the
real obstacle to judging a strategy, because a week of M15 cannot tell an edge
from luck.

Binance's public klines endpoint has no such limit. ``PAXGUSDT`` is tokenised
allocated gold — the same instrument Kraken serves, so the premium behaviour is
already understood — and its history runs back to 2020-08-28. Requests are
capped at 1000 bars each, so :meth:`fetch` pages backwards until it has enough
or the history runs out. That turns a one-week sample into a five-year one.

``data-api.binance.vision`` is used rather than ``api.binance.com``: the latter
answers 451 from some regions, the market-data host does not.
"""

from __future__ import annotations

from typing import Optional

from ..models import Bar, Series
from .base import Provider, ProviderError, clean_bars, http_get_json

BASE = "https://data-api.binance.vision/api/v3/klines"
TICKER = "https://data-api.binance.vision/api/v3/ticker/price"

INTERVALS = {"M1": "1m", "M5": "5m", "M15": "15m", "M30": "30m", "H1": "1h", "H4": "4h", "D1": "1d"}

PAGE = 1000  # the endpoint's hard maximum per request
# A backstop so a bug can never spin forever. 60 pages is 60,000 bars — years
# of daily data and months of M15.
MAX_PAGES = 60


class BinanceProvider(Provider):
    """Gold-backed token OHLC with deep, pageable history."""

    name = "binance:PAXGUSDT"
    always_on = True

    def __init__(self, pair: str = "PAXGUSDT") -> None:
        self.pair = pair

    def fetch(self, timeframe: str, bars: int) -> Series:
        interval = INTERVALS.get(timeframe.upper())
        if interval is None:
            raise ProviderError(f"unsupported timeframe {timeframe!r}")

        wanted = max(1, bars)
        # Walk backwards from now, one page at a time, until the request is
        # satisfied. Newest-first collection then reversed at the end.
        collected: list[Bar] = []
        end_time: Optional[int] = None
        for _ in range(MAX_PAGES):
            need = wanted - len(collected)
            if need <= 0:
                break
            limit = min(PAGE, need)
            url = f"{BASE}?symbol={self.pair}&interval={interval}&limit={limit}"
            if end_time is not None:
                url += f"&endTime={end_time}"
            rows = http_get_json(url)
            if not isinstance(rows, list) or not rows:
                break
            page = [self._bar(row) for row in rows]
            page = [b for b in page if b is not None]
            if not page:
                break
            collected = page + collected
            oldest = page[0].time * 1000
            if end_time is not None and oldest >= end_time:
                break  # no progress: the history is exhausted
            end_time = oldest - 1

        built = clean_bars(collected)
        series = Series(timeframe=timeframe.upper(), bars=built, source=self.name)
        if not series.bars:
            raise ProviderError("no usable bars")
        return series

    @staticmethod
    def _bar(row) -> Optional[Bar]:
        """One kline: ``[openTime, open, high, low, close, volume, ...]``."""
        try:
            return Bar(
                time=int(row[0]) // 1000,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5] or 0.0),
            )
        except (TypeError, ValueError, IndexError):
            return None

    def spot(self) -> Optional[float]:
        try:
            payload = http_get_json(f"{TICKER}?symbol={self.pair}")
            price = (payload or {}).get("price")
            return float(price) if price is not None else None
        except (ProviderError, TypeError, ValueError):
            return None
