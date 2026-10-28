"""gold-api.com — a keyless spot price, used only to cross-check the candles.

This source has no OHLC history, so it never drives structure detection. Its
one job is to answer "is the candle feed roughly where spot is?" — a cheap
sanity check that catches a stale or wildly wrong series before it reaches the
reader.
"""

from __future__ import annotations

from ..models import Series
from .base import Provider, ProviderError, http_get_json

BASE = "https://api.gold-api.com/price"


class GoldApiProvider(Provider):
    """Spot-only provider; ``fetch`` is intentionally unsupported."""

    name = "gold-api:XAU"
    always_on = True

    def __init__(self, symbol: str = "XAU") -> None:
        self.symbol = symbol

    def fetch(self, timeframe: str, bars: int) -> Series:
        raise ProviderError("gold-api serves spot only, not candles")

    def spot(self) -> float | None:
        payload = http_get_json(f"{BASE}/{self.symbol}")
        price = (payload or {}).get("price")
        if price is None:
            raise ProviderError("no price in response")
        return float(price)
