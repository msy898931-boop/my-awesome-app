"""Tests for the feed's selection, fallback and honesty paths.

``MarketFeed`` is the layer that decides *which* tape a read is built on, and
that decision was only partly covered (62%). The interesting paths are the ones
where something goes wrong: a provider that crashes rather than raising a clean
``ProviderError``, an archive that cannot be written, and a network that is
down entirely so the archive has to serve the read. None of this touches the
network — providers are stubs.
"""

from __future__ import annotations

from goldbot.data.base import ProviderError
from goldbot.data.feed import FeedReport, MarketFeed
from goldbot.models import Bar, Series


def _series(tf: str, times: list[int], source: str = "fake") -> Series:
    bars = [Bar(time=t, open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0) for t in times]
    return Series(timeframe=tf, bars=bars, source=source)


def _enough(tf: str, source: str, start: int = 1_000_000) -> Series:
    """A series that clears MIN_BARS comfortably."""
    return _series(tf, [start + i * 60 for i in range(60)], source=source)


class _Stub:
    """A provider that returns a canned series, or raises on demand."""

    always_on = True

    def __init__(self, name, series=None, raises=None, spot=None):
        self.name = name
        self._series = series
        self._raises = raises
        self._spot = spot

    def fetch(self, timeframe, bars):
        if self._raises is not None:
            raise self._raises
        return self._series

    def spot(self):
        return self._spot


# ── provider failure handling ───────────────────────────────────────────


def test_a_provider_that_crashes_is_recorded_not_fatal():
    """A provider bug must be logged as a crash, not take the whole feed down."""

    class _Crashing(_Stub):
        def fetch(self, timeframe, bars):
            raise ValueError("bug in the provider")

    good = _enough("H1", "good")
    feed = MarketFeed([_Crashing("boom"), _Stub("good", good)])
    chosen, report = feed.series("H1", 30)
    assert chosen.source == "good"
    assert any(name == "boom" and "crash" in outcome for name, outcome in report.attempts)


def test_a_thin_reply_is_recorded_and_skipped():
    thin = _series("H1", [1, 2, 3], source="thin")
    good = _enough("H1", "good")
    feed = MarketFeed([_Stub("thin", thin), _Stub("good", good)])
    chosen, report = feed.series("H1", 30)
    assert chosen.source == "good"
    assert any(name == "thin" and "thin" in outcome for name, outcome in report.attempts)


def test_no_provider_and_no_archive_returns_none():
    feed = MarketFeed([_Stub("down", raises=ProviderError("HTTP 503"))])
    chosen, report = feed.series("H1", 30)
    assert chosen is None
    assert report.chosen == ""
    assert report.ok is False
    assert any("503" in outcome for _, outcome in report.attempts)


# ── archive behaviour ───────────────────────────────────────────────────


class _FakeArchive:
    """An archive stand-in with scripted behaviour, so no disk is touched."""

    def __init__(self, stored: Series | None = None, merge_added: int = 0, write_raises=None):
        self._stored = stored
        self._merge_added = merge_added
        self._write_raises = write_raises
        self.merged: list[tuple[str, str]] = []

    def merge(self, timeframe, series):
        if self._write_raises is not None:
            raise self._write_raises
        self.merged.append((timeframe, series.source))
        return self._merge_added

    def series(self, timeframe, bars):
        return self._stored


def test_a_successful_read_is_folded_into_the_archive():
    good = _enough("H1", "good")
    archive = _FakeArchive(merge_added=5)
    feed = MarketFeed([_Stub("good", good)], archive=archive)
    _, report = feed.series("H1", 30)
    assert archive.merged == [("H1", "good")]
    assert any(name == "archive" and "+5" in outcome for name, outcome in report.attempts)


def test_an_archive_write_failure_is_reported_not_raised():
    good = _enough("H1", "good")
    archive = _FakeArchive(write_raises=OSError("disk full"))
    feed = MarketFeed([_Stub("good", good)], archive=archive)
    chosen, report = feed.series("H1", 30)
    assert chosen.source == "good"
    assert any(name == "archive" and "write failed" in outcome for name, outcome in report.attempts)


def test_the_archive_serves_the_read_when_every_provider_is_down():
    stored = _enough("H1", "from-archive")
    archive = _FakeArchive(stored=stored)
    feed = MarketFeed([_Stub("down", raises=ProviderError("offline"))], archive=archive)
    chosen, report = feed.series("H1", 30)
    assert chosen is stored
    assert report.chosen == "from-archive"
    assert any(name == "archive" and "served" in outcome for name, outcome in report.attempts)


def test_the_archive_is_offered_as_a_candidate_when_depth_is_preferred():
    """A backtest wants the longest sample, and the archive is usually it."""
    live = _series("H1", list(range(0, 3000, 100)), source="live")
    stored = _series("H1", list(range(0, 9000, 100)), source="archive-deep")
    archive = _FakeArchive(stored=stored)
    feed = MarketFeed([_Stub("live", live)], archive=archive)
    chosen, report = feed.series("H1", 200, prefer_depth=True)
    assert chosen.source == "archive-deep"
    assert any("candidate" in outcome for _, outcome in report.attempts)


def test_a_too_thin_archive_is_not_offered_as_a_candidate():
    live = _enough("H1", "live")
    stored = _series("H1", [1, 2, 3], source="thin-archive")
    archive = _FakeArchive(stored=stored)
    feed = MarketFeed([_Stub("live", live)], archive=archive)
    chosen, _ = feed.series("H1", 30, prefer_depth=True)
    assert chosen.source == "live"


# ── spot cross-check ────────────────────────────────────────────────────


def test_spot_check_is_silent_when_no_quote_answers():
    feed = MarketFeed([_Stub("no-spot", _enough("H1", "x"), spot=None)])
    assert feed.spot_check(_enough("H1", "x")) is None


def test_spot_check_flags_a_series_far_from_spot():
    series = _series("H1", [1000], source="x")
    series.bars[0] = Bar(time=1000, open=100, high=101, low=99, close=100.0, volume=1.0)
    feed = MarketFeed([_Stub("q", _enough("H1", "y"), spot=200.0)])
    out = feed.spot_check(series)
    assert out is not None
    assert out["trusted"] is False
    assert out["max_divergence_pct"] == 100.0


def test_spot_check_trusts_a_close_quote():
    series = _series("H1", [1000], source="x")
    series.bars[0] = Bar(time=1000, open=100, high=101, low=99, close=100.0, volume=1.0)
    feed = MarketFeed([_Stub("q", _enough("H1", "y"), spot=101.0)])
    out = feed.spot_check(series)
    assert out is not None and out["trusted"] is True


def test_spot_check_ignores_a_provider_that_raises():
    class _SpotBoom(_Stub):
        def spot(self):
            raise RuntimeError("spot endpoint died")

    series = _series("H1", [1000], source="x")
    series.bars[0] = Bar(time=1000, open=100, high=101, low=99, close=100.0, volume=1.0)
    feed = MarketFeed([_SpotBoom("boom", _enough("H1", "y"), spot=100.0)])
    # The crashing provider is skipped; with no other quote there is nothing to say.
    assert feed.spot_check(series) is None


def test_spot_check_returns_none_for_a_zero_close():
    series = _series("H1", [1000], source="x")
    series.bars[0] = Bar(time=1000, open=0, high=0, low=0, close=0.0, volume=0.0)
    feed = MarketFeed([_Stub("q", _enough("H1", "y"), spot=100.0)])
    assert feed.spot_check(series) is None


def test_spot_check_returns_none_for_an_empty_series():
    feed = MarketFeed([_Stub("q", _enough("H1", "y"), spot=100.0)])
    empty = Series(timeframe="H1", bars=[], source="empty")
    assert feed.spot_check(empty) is None


# ── live spot (the /سعر button) ─────────────────────────────────────────


def test_live_spot_reports_every_source_and_the_spread():
    feed = MarketFeed(
        [
            _Stub("a", spot=4000.0),
            _Stub("b", spot=4010.0),
            _Stub("c", spot=4020.0),
        ]
    )
    out = feed.live_spot()
    assert out["checked"] is True
    assert out["sources"] == 3
    assert out["quotes"] == {"a": 4000.0, "b": 4010.0, "c": 4020.0}
    assert out["low"] == 4000.0 and out["high"] == 4020.0
    assert out["spread_pct"] == 0.499  # 20 / 4010 * 100


def test_live_spot_uses_the_median_so_one_outlier_cannot_move_it():
    """Yahoo's futures quote carries a contango premium; it must not lead."""
    feed = MarketFeed(
        [_Stub("a", spot=4000.0), _Stub("b", spot=4001.0), _Stub("yahoo", spot=4200.0)]
    )
    out = feed.live_spot()
    assert out["price"] == 4001.0
    assert out["high"] == 4200.0


def test_live_spot_averages_the_middle_two_for_an_even_count():
    feed = MarketFeed([_Stub("a", spot=4000.0), _Stub("b", spot=4010.0)])
    out = feed.live_spot()
    assert out["price"] == 4005.0


def test_live_spot_says_the_check_ran_when_nothing_answers():
    """An empty read is 'we looked and found nothing', never a quiet zero."""
    feed = MarketFeed([_Stub("down", spot=None)])
    out = feed.live_spot()
    assert out["checked"] is True
    assert out["price"] is None
    assert out["sources"] == 0
    assert out["quotes"] == {}


def test_live_spot_skips_a_provider_that_raises():
    class _Boom(_Stub):
        def spot(self):
            raise RuntimeError("spot endpoint died")

    feed = MarketFeed([_Boom("boom"), _Stub("good", spot=4000.0)])
    out = feed.live_spot()
    assert out["quotes"] == {"good": 4000.0}
    assert out["price"] == 4000.0


def test_spot_quotes_is_the_shared_seam_for_both_reads():
    """spot_check and live_spot must agree on which sources answered."""
    feed = MarketFeed([_Stub("a", spot=4000.0), _Stub("b", spot=None)])
    assert feed.spot_quotes() == {"a": 4000.0}


# ── staleness ───────────────────────────────────────────────────────────


def test_staleness_is_measured_against_the_bar_step():
    import time as _time

    now = int(_time.time())
    # A bar that opened one step (60m) ago is current: the clock has barely
    # moved past the step, so the answer is ~0 rather than a minute count.
    series = _series("H1", [now - 3600], source="x")
    feed = MarketFeed([_Stub("q", series)])
    minutes = feed.staleness_minutes(series, "H1")
    assert minutes is not None and minutes < 1.0


def test_staleness_grows_when_the_tape_is_frozen():
    import time as _time

    now = int(_time.time())
    series = _series("H1", [now - 3600 * 5], source="x")
    feed = MarketFeed([_Stub("q", series)])
    minutes = feed.staleness_minutes(series, "H1")
    assert minutes is not None and minutes > 200


def test_staleness_is_none_for_an_empty_series():
    feed = MarketFeed([])
    assert feed.staleness_minutes(Series(timeframe="H1", bars=[], source="x"), "H1") is None


def test_staleness_defaults_to_the_hour_step_for_an_unknown_frame():
    import time as _time

    now = int(_time.time())
    series = _series("W1", [now - 3600], source="x")
    feed = MarketFeed([])
    minutes = feed.staleness_minutes(series, "W1")
    assert minutes is not None and minutes < 1.0


def test_feed_report_ok_reflects_whether_a_source_was_chosen():
    assert FeedReport(timeframe="H1", chosen="kraken").ok is True
    assert FeedReport(timeframe="H1").ok is False
