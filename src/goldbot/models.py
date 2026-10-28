"""Shared value objects.

A ``Bar`` is the only shape the analysis code ever sees. Everything upstream
(data providers) and downstream (SMC, strategy, bot) speaks in these terms, so
a provider can be swapped without touching the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Bar:
    """One OHLC candle. ``time`` is the bar's **open** time, in Unix seconds."""

    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "time": self.time,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Bar | None:
        try:
            return cls(
                time=int(row["time"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", 0.0) or 0.0),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def is_sane(self) -> bool:
        """A bar whose prices are positive, ordered and finite. Junk is dropped,
        not repaired."""
        prices = (self.open, self.high, self.low, self.close)
        if any(p != p or p in (float("inf"), float("-inf")) for p in prices):
            return False
        # A price is never zero or negative. A provider's empty seed row parses
        # to 0.0, and letting it through poisons ATR and every structure level.
        if any(p <= 0 for p in prices):
            return False
        return self.high >= max(self.open, self.close) and self.low <= min(self.open, self.close)


@dataclass
class Quote:
    """A spot read, used to sanity-check the candle feed."""

    price: float
    source: str
    time: int = 0


@dataclass
class Series:
    """A named set of bars for one timeframe, oldest first."""

    timeframe: str
    bars: list[Bar] = field(default_factory=list)
    source: str = ""

    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def __len__(self) -> int:
        return len(self.bars)
