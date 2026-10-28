"""Market data acquisition — keyless, multi-source, self-validating.

Why more than one provider: no single free feed is both live and exact for
spot gold. Yahoo carries COMEX futures (``GC=F``), which trade nearly 24/5 and
are the deepest free series available; Kraken carries ``PAXG``, a
gold-backed token that trades 24/7 and tracks spot closely; gold-api.com gives
a keyless spot price for cross-checking. The engine reads all enabled sources,
prefers the one whose newest bar is freshest *and* sane, and records which one
it used so a read is never anonymous.
"""

from __future__ import annotations

from .base import Provider, ProviderError
from .binance import BinanceProvider
from .feed import MarketFeed, build_feed
from .goldapi import GoldApiProvider
from .kraken import KrakenProvider
from .yahoo import YahooProvider

__all__ = [
    "Provider",
    "ProviderError",
    "YahooProvider",
    "KrakenProvider",
    "BinanceProvider",
    "GoldApiProvider",
    "MarketFeed",
    "build_feed",
]
