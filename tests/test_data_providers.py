"""Tests for the paging Binance provider and depth-aware feed selection.

No network: ``http_get_json`` is monkeypatched with canned klines, so a failure
here means the paging or the selection rule broke, not that a host was down.

Two behaviours matter and are pinned:

* paging must walk backwards until the request is satisfied, without
  duplicating or looping forever;
* the feed must pick the *freshest* series for live reads and the *longest* one
  for a backtest, since those are opposite criteria.
"""

from __future__ import annotations

import pytest

from goldbot.data.binance import BinanceProvider
from goldbot.data.feed import MarketFeed
from goldbot.models import Bar, Series


def _klines(start: int, count: int, step_ms: int = 3_600_000):
    """Canned kline rows: [openTime(ms), o, h, l, c, v, ...]."""
    rows = []
    for i in range(count):
        t = start + i * step_ms
        rows.append(
            [t, "2000.0", "2010.0", "1990.0", "2005.0", "12.5", t + step_ms, "0", 1, "0", "0", "0"]
        )
    return rows


class _FakeSeries(Series):
    pass


def _series(tf: str, times: list[int], source: str = "fake") -> Series:
    bars = [Bar(time=t, open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0) for t in times]
    return Series(timeframe=tf, bars=bars, source=source)


def test_binance_pages_backwards_until_satisfied(monkeypatch):
    """Asking for 2500 bars from a 2500-bar history must page three times."""
    step_ms = 3_600_000
    base = 1_600_000_000_000
    history = [base + i * step_ms for i in range(2500)]  # oldest first
    calls: list[str] = []

    def fake_get(url, timeout=15.0):
        calls.append(url)
        if "endTime=" in url:
            end_ms = int(url.split("endTime=")[1])
            window = [t for t in history if t <= end_ms]
        else:
            window = history
        newest = window[-1000:] if len(window) > 1000 else window
        return _klines(newest[0], len(newest), step_ms)

    monkeypatch.setattr("goldbot.data.binance.http_get_json", fake_get)

    series = BinanceProvider().fetch("H1", 2500)
    assert len(calls) == 3
    assert len(series.bars) == 2500
    times = [b.time for b in series.bars]
    assert times == sorted(times)
    assert len(set(times)) == len(times)
    # The oldest bar of the whole history is included: paging reached the end.
    assert times[0] == base // 1000


def test_binance_stops_when_history_runs_out(monkeypatch):
    """A page that makes no progress must end the walk, not spin forever."""
    calls = {"n": 0}

    def fake_get(url, timeout=15.0):
        calls["n"] += 1
        return _klines(1_700_000_000_000, 10)

    monkeypatch.setattr("goldbot.data.binance.http_get_json", fake_get)
    series = BinanceProvider().fetch("H1", 5000)
    assert calls["n"] < 5  # bounded, not MAX_PAGES
    assert len(series.bars) == 10


def test_binance_rejects_unknown_timeframe():
    from goldbot.data.base import ProviderError

    with pytest.raises(ProviderError):
        BinanceProvider().fetch("W1", 100)


def test_binance_skips_malformed_rows(monkeypatch):
    def fake_get(url, timeout=15.0):
        good = _klines(1_700_000_000_000, 3)
        good.append([None, None, None, None, None])
        good.append([1_700_000_100_000, "bad", "bad", "bad", "bad"])
        return good

    monkeypatch.setattr("goldbot.data.binance.http_get_json", fake_get)
    series = BinanceProvider().fetch("H1", 10)
    assert len(series.bars) == 3


class _StubProvider:
    """A provider that returns whatever it was told to, without any HTTP."""

    always_on = True

    def __init__(self, name, series):
        self.name = name
        self._series = series

    def fetch(self, timeframe, bars):
        return self._series

    def spot(self):
        return None


def test_feed_prefers_freshest_for_live_reads():
    old = _series("H1", [1000, 2000, 3000] * 20, source="old-long")
    # Both must clear the minimum-bar floor, so the *only* difference is which
    # end of the tape they cover.
    fresh = _series("H1", [900_000 + i * 1000 for i in range(40)], source="fresh-short")
    feed = MarketFeed([_StubProvider("old", old), _StubProvider("fresh", fresh)])
    chosen, _ = feed.series("H1", 100)
    assert chosen.source == "fresh-short"


def test_feed_prefers_longest_for_backtests():
    old = _series("H1", [1000, 2000, 3000] * 20, source="old-long")
    fresh = _series("H1", [900_000 + i * 1000 for i in range(40)], source="fresh-short")
    feed = MarketFeed([_StubProvider("old", old), _StubProvider("fresh", fresh)])
    chosen, _ = feed.series("H1", 100, prefer_depth=True)
    assert chosen.source == "old-long"
    assert len(chosen.bars) == 60


def test_feed_reports_why_a_provider_was_skipped():
    from goldbot.data.base import ProviderError

    class _Broken:
        always_on = False
        name = "broken"

        def fetch(self, timeframe, bars):
            raise ProviderError("HTTP 429")

        def spot(self):
            return None

    good = _series("H1", list(range(0, 3000, 100)), source="good")
    feed = MarketFeed([_Broken(), _StubProvider("good", good)])
    chosen, report = feed.series("H1", 100)
    assert chosen.source == "good"
    assert any(name == "broken" and "429" in outcome for name, outcome in report.attempts)
