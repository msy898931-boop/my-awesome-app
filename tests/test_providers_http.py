"""Tests for the HTTP-backed providers that the feed falls back to.

Yahoo was the least covered module in the project (23%): it is off by default,
so nothing exercised it. But "off by default" is a setting, not a guarantee —
``GOLDBOT_ENABLE_YAHOO=1`` turns it on, and then its parsing, its H4 resample
and its spot read all matter. The same applies to gold-api (spot only) and
Binance's spot call. ``http_get_json`` is monkeypatched throughout, so a failure
here is a parsing bug, never a network one.
"""

from __future__ import annotations

import pytest

from goldbot.data.base import ProviderError, http_get_json
from goldbot.data.goldapi import GoldApiProvider
from goldbot.data.yahoo import YahooProvider, _resample
from goldbot.models import Bar, Series


def _payload(opens, closes, stamps, highs=None, lows=None, volumes=None):
    n = len(stamps)
    if highs is None:
        highs = [
            max(o, c) + 10.0 if o is not None and c is not None else None
            for o, c in zip(opens, closes, strict=False)
        ]
    if lows is None:
        lows = [
            min(o, c) - 10.0 if o is not None and c is not None else None
            for o, c in zip(opens, closes, strict=False)
        ]
    return {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": opens,
                                "high": highs,
                                "low": lows,
                                "close": closes,
                                "volume": volumes if volumes is not None else [0.0] * n,
                            }
                        ]
                    },
                }
            ],
            "error": None,
        }
    }


# ── yahoo: parsing ──────────────────────────────────────────────────────


def test_yahoo_rejects_an_unknown_timeframe(monkeypatch):
    with pytest.raises(ProviderError, match="unsupported timeframe"):
        YahooProvider().fetch("W1", 100)


def test_yahoo_builds_bars_from_a_chart_payload(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.yahoo.http_get_json",
        lambda url, timeout=15.0: _payload(
            [2000.0], [2005.0], [1_600_000_000], highs=[2010.0], lows=[1990.0]
        ),
    )
    series = YahooProvider().fetch("H1", 100)
    assert series.source == "yahoo:GC=F"
    assert len(series.bars) == 1
    bar = series.bars[0]
    assert (bar.open, bar.high, bar.low, bar.close) == (2000.0, 2010.0, 1990.0, 2005.0)


def test_yahoo_raises_when_the_result_is_empty(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.yahoo.http_get_json",
        lambda url, timeout=15.0: {"chart": {"result": None, "error": {"description": "delisted"}}},
    )
    with pytest.raises(ProviderError, match="delisted"):
        YahooProvider().fetch("H1", 100)


def test_yahoo_reports_an_empty_result_without_an_error_node(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: {"chart": {"result": []}}
    )
    with pytest.raises(ProviderError, match="empty result"):
        YahooProvider().fetch("H1", 100)


def test_yahoo_skips_a_row_with_a_null_close(monkeypatch):
    payload = _payload([2000.0, 2001.0], [None, 2002.0], [1, 2])
    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: payload)
    series = YahooProvider().fetch("H1", 100)
    assert len(series.bars) == 1 and series.bars[0].close == 2002.0


def test_yahoo_falls_back_to_open_when_high_or_low_is_null(monkeypatch):
    # With both high and low null they fall back to the open, which is only a
    # sane bar when the close equals the open — so the row is flat here.
    payload = _payload([2000.0], [2000.0], [1], highs=[None], lows=[None])
    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: payload)
    bar = YahooProvider().fetch("H1", 100).bars[0]
    assert bar.high == 2000.0 and bar.low == 2000.0


def test_yahoo_raises_when_no_row_is_usable(monkeypatch):
    payload = _payload([None], [None], [1])
    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: payload)
    with pytest.raises(ProviderError, match="no usable bars"):
        YahooProvider().fetch("H1", 100)


def test_yahoo_uses_an_epoch_window_for_intraday(monkeypatch):
    seen: dict = {}

    def fake(url, timeout=15.0):
        seen["url"] = url
        return _payload([2000.0], [2005.0], [1])

    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", fake)
    YahooProvider().fetch("M15", 100)
    assert "period1=" in seen["url"] and "period2=" in seen["url"]


def test_yahoo_trims_to_the_requested_bar_count(monkeypatch):
    stamps = list(range(1, 51))
    payload = _payload([2000.0] * 50, [2005.0] * 50, stamps)
    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: payload)
    series = YahooProvider().fetch("H1", 10)
    assert len(series.bars) == 10
    assert series.bars[-1].time == 50


# ── yahoo: H4 resample ──────────────────────────────────────────────────


def _hourly(n, start=1_600_000_000):
    base = start - (start % 3600)
    return [
        Bar(
            time=base + i * 3600,
            open=2000.0 + i,
            high=2005.0 + i,
            low=1995.0 + i,
            close=2002.0 + i,
            volume=1.0,
        )
        for i in range(n)
    ]


def test_resample_builds_a_full_h4_candle():
    series = Series(timeframe="H4", bars=_hourly(8), source="t")
    out = _resample(series, 4)
    assert len(out.bars) == 2
    first = out.bars[0]
    assert first.open == 2000.0  # open of the first hour
    assert first.close == 2005.0  # close of the fourth hour
    assert first.high == 2008.0 and first.low == 1995.0
    assert first.volume == 4.0


def test_resample_drops_a_partial_trailing_bucket():
    """A half-formed candle must never masquerade as closed."""
    series = Series(timeframe="H4", bars=_hourly(6), source="t")  # 4 + 2
    out = _resample(series, 4)
    assert len(out.bars) == 1


def test_resample_keeps_the_source_and_timeframe():
    series = Series(timeframe="H4", bars=_hourly(4), source="yahoo:GC=F")
    out = _resample(series, 4)
    assert out.source == "yahoo:GC=F" and out.timeframe == "H4"


def test_yahoo_h4_is_resampled_end_to_end(monkeypatch):
    stamps = [1_600_000_000 + i * 3600 for i in range(8)]
    payload = _payload([2000.0] * 8, [2005.0] * 8, stamps)
    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", lambda url, timeout=15.0: payload)
    series = YahooProvider().fetch("H4", 100)
    assert series.timeframe == "H4"
    assert len(series.bars) <= 2


# ── yahoo: spot ─────────────────────────────────────────────────────────


def test_yahoo_spot_reads_the_regular_market_price(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.yahoo.http_get_json",
        lambda url, timeout=15.0: {"chart": {"result": [{"meta": {"regularMarketPrice": 2050.5}}]}},
    )
    assert YahooProvider().spot() == 2050.5


def test_yahoo_spot_is_none_without_a_price(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.yahoo.http_get_json",
        lambda url, timeout=15.0: {"chart": {"result": [{"meta": {}}]}},
    )
    assert YahooProvider().spot() is None


def test_yahoo_spot_swallows_a_provider_error(monkeypatch):
    def _boom(url, timeout=15.0):
        raise ProviderError("HTTP 429")

    monkeypatch.setattr("goldbot.data.yahoo.http_get_json", _boom)
    assert YahooProvider().spot() is None


# ── gold-api ────────────────────────────────────────────────────────────


def test_goldapi_never_serves_candles():
    with pytest.raises(ProviderError, match="spot only"):
        GoldApiProvider().fetch("H1", 100)


def test_goldapi_returns_the_spot_price(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.goldapi.http_get_json",
        lambda url, timeout=15.0: {"price": 2051.25},
    )
    assert GoldApiProvider().spot() == 2051.25


def test_goldapi_raises_when_the_price_is_missing(monkeypatch):
    monkeypatch.setattr("goldbot.data.goldapi.http_get_json", lambda url, timeout=15.0: {})
    with pytest.raises(ProviderError, match="no price"):
        GoldApiProvider().spot()


# ── http_get_json ───────────────────────────────────────────────────────


class _Resp:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_get_json_parses_a_body(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp(b'{"a": 1}'))
    assert http_get_json("http://x") == {"a": 1}


def test_http_get_json_rejects_a_non_200(monkeypatch):
    import urllib.request

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"{}", status=503)
    )
    with pytest.raises(ProviderError, match="HTTP 503"):
        http_get_json("http://x")


def test_http_get_json_translates_an_http_error(monkeypatch):
    import urllib.error
    import urllib.request

    def _raise(req, timeout=None):
        raise urllib.error.HTTPError("http://x", 429, "slow down", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(ProviderError, match="HTTP 429"):
        http_get_json("http://x")


def test_http_get_json_translates_a_network_error(monkeypatch):
    import urllib.error
    import urllib.request

    def _raise(req, timeout=None):
        raise urllib.error.URLError("dns")

    monkeypatch.setattr(urllib.request, "urlopen", _raise)
    with pytest.raises(ProviderError, match="network"):
        http_get_json("http://x")


def test_http_get_json_translates_bad_json(monkeypatch):
    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp(b"not json"))
    with pytest.raises(ProviderError, match="bad JSON"):
        http_get_json("http://x")


def test_http_get_json_sends_a_browser_like_user_agent(monkeypatch):
    """Cloudflare 403s (error 1010) the default urllib UA, so it must not be used."""
    import urllib.request

    seen: dict = {}

    def fake(req, timeout=None):
        seen["ua"] = req.get_header("User-agent")
        return _Resp(b"{}")

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    http_get_json("http://x")
    assert "Mozilla" in seen["ua"]


# ── binance spot ────────────────────────────────────────────────────────


def test_binance_spot_reads_the_price(monkeypatch):
    from goldbot.data.binance import BinanceProvider

    monkeypatch.setattr(
        "goldbot.data.binance.http_get_json", lambda url, timeout=15.0: {"price": "2050.10"}
    )
    assert BinanceProvider().spot() == 2050.10


def test_binance_spot_is_none_when_the_payload_is_empty(monkeypatch):
    from goldbot.data.binance import BinanceProvider

    monkeypatch.setattr("goldbot.data.binance.http_get_json", lambda url, timeout=15.0: {})
    assert BinanceProvider().spot() is None


def test_binance_spot_swallows_a_provider_error(monkeypatch):
    from goldbot.data.binance import BinanceProvider

    def _boom(url, timeout=15.0):
        raise ProviderError("HTTP 451")

    monkeypatch.setattr("goldbot.data.binance.http_get_json", _boom)
    assert BinanceProvider().spot() is None


def test_binance_bar_returns_none_for_a_malformed_row():
    from goldbot.data.binance import BinanceProvider

    assert BinanceProvider._bar(["x", "y"]) is None
    assert BinanceProvider._bar([]) is None


def test_binance_bar_parses_a_well_formed_row():
    from goldbot.data.binance import BinanceProvider

    bar = BinanceProvider._bar([1_600_000_000_000, "2000", "2010", "1990", "2005", "12.5"])
    assert bar is not None
    assert bar.time == 1_600_000_000
    assert bar.close == 2005.0


def test_binance_returns_no_usable_bars_when_every_row_is_bad(monkeypatch):
    from goldbot.data.binance import BinanceProvider

    monkeypatch.setattr(
        "goldbot.data.binance.http_get_json",
        lambda url, timeout=15.0: [["bad"], ["worse"]],
    )
    with pytest.raises(ProviderError, match="no usable bars"):
        BinanceProvider().fetch("H1", 10)
