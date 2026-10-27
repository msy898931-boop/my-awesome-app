"""Shared test fixtures.

The suite deliberately avoids the network: every test runs against synthetic
bars with known answers, so a failure means the logic broke, not that a feed
was down.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from goldbot.models import Bar  # noqa: E402


def make_bars(prices: list[tuple[float, float, float, float]], start: int = 1_700_000_000,
              step: int = 900) -> list[Bar]:
    """Bars from (open, high, low, close) tuples, stamped at a fixed interval."""
    return [
        Bar(time=start + i * step, open=o, high=h, low=lo, close=c, volume=100.0)
        for i, (o, h, lo, c) in enumerate(prices)
    ]


@pytest.fixture
def ramp_up():
    """A monotonic ramp: no interior pivots, so it is useful only for
    premium/discount and ATR assertions."""
    prices = []
    price = 2000.0
    for _ in range(80):
        o = price
        c = price + 3.0
        prices.append((o, c + 1.0, o - 1.0, c))
        price = c
    return make_bars(prices)


def _leg(price: float, step: float, bars: int) -> tuple[list[tuple], float]:
    """One directional leg. OHLC is derived from open/close so it is always sane.

    The wick on the opening side is kept small: otherwise the reversal bar's
    wick ties with the pivot's, and a *strict* fractal test — the correct one —
    finds no pivot at all.
    """
    out = []
    for _ in range(bars):
        o = price
        c = price + step
        if step > 0:
            out.append((o, c + 0.5, o - 0.2, c))
        else:
            out.append((o, o + 0.2, c - 0.5, c))
        price = c
    return out, price


def _zigzag(start: float, up_bars: int, up_step: float, down_bars: int,
            down_step: float, legs: int) -> list[Bar]:
    """Alternating impulse and pullback legs — the shape that actually produces
    fractal pivots, and therefore structure events."""
    prices: list[tuple] = []
    price = start
    for _ in range(legs):
        leg, price = _leg(price, up_step, up_bars)
        prices += leg
        leg, price = _leg(price, down_step, down_bars)
        prices += leg
    return make_bars(prices)


@pytest.fixture
def trending_up():
    """A zigzag uptrend: higher highs and higher lows, with clear pivots."""
    return _zigzag(2000.0, 4, 5.0, 3, -4.0, 10)


@pytest.fixture
def trending_down():
    """The mirror: lower highs and lower lows."""
    return _zigzag(2000.0, 4, -5.0, 3, 4.0, 10)


@pytest.fixture
def flat_market():
    """A range: no structure, which must produce no trade rather than a guess."""
    prices = []
    for i in range(80):
        base = 2000.0 + (2.0 if i % 2 else -2.0)
        prices.append((2000.0, base + 0.5, base - 0.5, base))
    return make_bars(prices)
