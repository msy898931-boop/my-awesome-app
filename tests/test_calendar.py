"""Tests for the economic calendar's failure reporting.

The bug these cover: a rate-limited fetch returned an empty list, which is
indistinguishable from a quiet week. A scheduled FOMC release went missing and
the card still called the window calm, because "no events" and "no feed" looked
identical in the data. These tests pin the distinction.
"""

from __future__ import annotations

import urllib.error

from goldbot.news.calendar import CalendarFeed, _retry_after
from goldbot.news.window import digest
from goldbot.telegram.format import format_news

ROWS = [
    {
        "title": "Federal Funds Rate",
        "country": "USD",
        "impact": "High",
        "date": "2026-10-28T14:00:00-04:00",
        "forecast": "4.00%",
        "previous": "4.00%",
    },
    {
        "title": "German GfK Consumer Climate",
        "country": "EUR",
        "impact": "Low",
        "date": "2026-10-28T03:00:00-04:00",
        "forecast": "-1.0",
        "previous": "-0.5",
    },
]


class _Resp:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    def read(self):
        import json

        return json.dumps(self._payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_a_good_fetch_reports_ok_and_parses_events():
    feed = CalendarFeed()
    feed._fetch = lambda: (ROWS, "")
    events = feed.events()
    assert feed.ok is True
    assert feed.error == ""
    assert len(events) == 2
    assert [e.title for e in feed.high_impact()] == ["Federal Funds Rate"]


def test_a_429_is_reported_not_swallowed():
    def boom():
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "30"}, None)

    feed = CalendarFeed()
    # Exercise the real _fetch, with urlopen raising as Cloudflare does.
    import goldbot.news.calendar as cal

    orig = cal.urllib.request.urlopen
    cal.urllib.request.urlopen = lambda *a, **k: boom()
    try:
        assert feed.events() == []
        assert feed.ok is False
        assert feed.error == "HTTP 429"
    finally:
        cal.urllib.request.urlopen = orig


def test_a_failed_fetch_keeps_the_previous_read_as_stale():
    feed = CalendarFeed()
    feed._fetch = lambda: (ROWS, "")
    feed.events()

    feed._fetch = lambda: ([], "HTTP 429")
    # ttl=0 forces a refetch, since the cache from the good read is still warm.
    kept = feed.events(ttl=0)
    assert [e.title for e in kept] == ["Federal Funds Rate", "German GfK Consumer Climate"]
    assert feed.ok is False
    assert feed.stale is True


def test_retry_after_is_read_and_clamped():
    assert _retry_after({"Retry-After": "25"}) == 25.0
    assert _retry_after({"Retry-After": "not-a-number"}) == 0.0
    assert _retry_after({}) == 0.0
    assert _retry_after({"Retry-After": "99999"}) == 3600.0


def test_backoff_blocks_the_next_attempt_after_a_429():
    import goldbot.news.calendar as cal

    feed = CalendarFeed()

    def rate_limited(*a, **k):
        raise urllib.error.HTTPError("u", 429, "Too Many Requests", {"Retry-After": "30"}, None)

    orig = cal.urllib.request.urlopen
    cal.urllib.request.urlopen = rate_limited
    try:
        feed.events()
        assert feed.error == "HTTP 429"
        # The next attempt is refused locally, without another request.
        rows, error = feed._fetch()
        assert rows == []
        assert "backing off" in error
    finally:
        cal.urllib.request.urlopen = orig


def test_a_failed_calendar_does_not_render_as_calm(monkeypatch):
    class _Dead:
        ok = False
        error = "HTTP 429"
        stale = False

        def high_impact(self):
            return []

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Dead(), _Rss()))
    card = format_news(digest())
    assert "السوق خالٍ" not in card
    assert "تعذّر فحص التقويم" in card
    assert "HTTP 429" in card


def test_a_checked_calendar_can_still_render_as_calm(monkeypatch):
    class _Live:
        ok = True
        error = ""
        stale = False

        def high_impact(self):
            return []

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Live(), _Rss()))
    card = format_news(digest())
    assert "السوق خالٍ من الأخبار المهمة" in card
    assert "تعذّر فحص التقويم" not in card


def test_the_card_never_prints_a_bare_none_for_a_missing_forecast(monkeypatch):
    class _Live:
        ok = True
        error = ""
        stale = False

        def high_impact(self):
            from goldbot.news.calendar import _parse

            return [e for e in (_parse(r) for r in ROWS) if e]

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Live(), _Rss()))
    card = format_news(digest(now=0))
    assert "None" not in card
    assert "Federal Funds Rate" in card


class _Rss:
    ok = True
    error = ""
    stale = False

    def relevant(self, limit=8):
        return []
