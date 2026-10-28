"""Tests for the Kraken feed — the live, always-on candle source.

Kraken is what the bot reads when COMEX is shut, so a silent break here would
leave the card describing a market it never actually saw. ``http_get_json`` is
monkeypatched with canned payloads; nothing here touches the network.

The behaviours pinned: the interval mapping, the error envelope, the row shape
(``[time, o, h, l, c, vwap, volume, count]``), the sane-bar filter, and the
ticker's close parsing.
"""

from __future__ import annotations

import pytest

from goldbot.data.base import ProviderError
from goldbot.data.kraken import INTERVALS, KrakenProvider


def _row(t: int, o=4000.0, h=4010.0, low=3990.0, c=4005.0, vol=1.0):
    # Kraken's 8-field row; index 5 (vwap) and 7 (count) are ignored.
    return [t, str(o), str(h), str(low), str(c), str(o), str(vol), 12]


def _ohlc(rows, pair="PAXGUSD"):
    return {"error": [], "result": {pair: rows, "last": rows[-1][0] if rows else 0}}


def test_fetch_maps_a_row_to_a_bar(monkeypatch):
    rows = [_row(1700000000), _row(1700000900, c=4010.0)]
    monkeypatch.setattr("goldbot.data.kraken.http_get_json", lambda url: _ohlc(rows))
    series = KrakenProvider().fetch("M15", 2)
    assert series.timeframe == "M15"
    assert series.source == "kraken:PAXGUSD"
    assert len(series.bars) == 2
    bar = series.bars[0]
    assert (bar.time, bar.open, bar.high, bar.low, bar.close, bar.volume) == (
        1700000000,
        4000.0,
        4010.0,
        3990.0,
        4005.0,
        1.0,
    )


def test_unsupported_timeframe_raises(monkeypatch):
    monkeypatch.setattr("goldbot.data.kraken.http_get_json", lambda url: _ohlc([]))
    with pytest.raises(ProviderError, match="unsupported timeframe"):
        KrakenProvider().fetch("W1", 10)


def test_the_error_envelope_becomes_a_provider_error(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.kraken.http_get_json",
        lambda url: {"error": ["EQuery:Unknown asset pair"], "result": {}},
    )
    with pytest.raises(ProviderError, match="Unknown asset pair"):
        KrakenProvider().fetch("M15", 10)


def test_an_empty_result_raises(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.kraken.http_get_json", lambda url: {"error": [], "result": {}}
    )
    with pytest.raises(ProviderError):
        KrakenProvider().fetch("M15", 10)


def test_insane_bars_are_dropped(monkeypatch):
    """A zero-price seed bar must not reach structure detection."""
    rows = [_row(1700000000, o=0.0, h=0.0, low=0.0, c=0.0), _row(1700000900)]
    monkeypatch.setattr("goldbot.data.kraken.http_get_json", lambda url: _ohlc(rows))
    series = KrakenProvider().fetch("M15", 10)
    assert [b.time for b in series.bars] == [1700000900]


def test_the_requested_depth_is_respected(monkeypatch):
    rows = [_row(1700000000 + i * 900) for i in range(50)]
    monkeypatch.setattr("goldbot.data.kraken.http_get_json", lambda url: _ohlc(rows))
    series = KrakenProvider().fetch("M15", 10)
    assert len(series.bars) == 10
    assert series.bars[-1].time == 1700000000 + 49 * 900, "the tail, not the head"


def test_the_interval_is_sent_in_minutes(monkeypatch):
    seen = {}

    def fake(url):
        seen["url"] = url
        return _ohlc([_row(1700000000)])

    monkeypatch.setattr("goldbot.data.kraken.http_get_json", fake)
    KrakenProvider().fetch("H4", 1)
    assert f"interval={INTERVALS['H4']}" in seen["url"]
    assert "pair=PAXGUSD" in seen["url"]


def test_spot_reads_the_close_of_the_ticker(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.kraken.http_get_json",
        lambda url: {"error": [], "result": {"PAXGUSD": {"c": ["4039.24", "0.0012"]}}},
    )
    assert KrakenProvider().spot() == 4039.24


def test_spot_returns_none_when_the_ticker_fails(monkeypatch):
    def boom(url):
        raise ProviderError("down")

    monkeypatch.setattr("goldbot.data.kraken.http_get_json", boom)
    assert KrakenProvider().spot() is None


def test_spot_returns_none_on_an_empty_close(monkeypatch):
    monkeypatch.setattr(
        "goldbot.data.kraken.http_get_json",
        lambda url: {"error": [], "result": {"PAXGUSD": {"c": []}}},
    )
    assert KrakenProvider().spot() is None
