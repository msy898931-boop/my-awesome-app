"""Tests for the economic calendar's failure reporting.

The bug these cover: a rate-limited fetch returned an empty list, which is
indistinguishable from a quiet week. A scheduled FOMC release went missing and
the card still called the window calm, because "no events" and "no feed" looked
identical in the data. These tests pin the distinction.
"""

from __future__ import annotations

import time
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
        usable = False

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
        usable = True

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
        usable = True

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


# ── the saved read: a blocked fetch must not silence the bot ─────────────
#
# The source rate-limits by IP, so a 429 is routine. Without a saved read a
# blocked fetch meant "could not check the calendar", which the blackout turns
# into "withhold the idea" — a transient block on the feed produced no ideas at
# all. These tests pin the fallback and, just as important, its limits: it must
# never serve a week that is over, and it must never replace a live fetch.


FUTURE = "2030-01-02T14:00:00-04:00"  # far ahead, so it survives any real clock


def _future_rows():
    return [
        {
            "title": "Federal Funds Rate",
            "country": "USD",
            "impact": "High",
            "date": FUTURE,
            "forecast": "4.00%",
            "previous": "4.00%",
        }
    ]


def _store_with(tmp_path, events, saved_at):
    from goldbot.news.calendar import CalendarStore

    store = CalendarStore(tmp_path / "calendar.json")
    store.save(events, now=saved_at)
    return store


def test_a_saved_read_is_restored_and_served_when_the_fetch_fails(tmp_path):
    from goldbot.news.calendar import _parse

    events = [e for e in (_parse(r) for r in _future_rows()) if e]
    store = _store_with(tmp_path, events, saved_at=time.time())

    feed = CalendarFeed(store=store)
    feed._fetch = lambda: ([], "HTTP 429")
    got = feed.events()

    assert [e.title for e in got] == ["Federal Funds Rate"]
    assert feed.ok is False, "a saved read is not a successful fetch"
    assert feed.stale is True, "and it must be named as stale"
    assert feed.usable is True, "but it is still something to reason about"
    assert feed.error == "HTTP 429", "the failure is still reported"


def test_a_saved_read_still_attempts_a_live_fetch(tmp_path):
    """The fallback is a fallback, never a reason to skip the source."""
    from goldbot.news.calendar import _parse

    events = [e for e in (_parse(r) for r in _future_rows()) if e]
    store = _store_with(tmp_path, events, saved_at=time.time())
    feed = CalendarFeed(store=store)

    calls = {"n": 0}

    def counted():
        calls["n"] += 1
        return [], "HTTP 429"

    feed._fetch = counted
    feed.events()
    assert calls["n"] == 1, "the source is still asked first"


def test_a_successful_fetch_overwrites_the_saved_read(tmp_path):
    from goldbot.news.calendar import CalendarStore

    store = CalendarStore(tmp_path / "calendar.json")
    feed = CalendarFeed(store=store)
    feed._fetch = lambda: (_future_rows(), "")
    feed.events()

    assert store.path.exists()
    restored = store.load()
    assert [e.title for e in restored] == ["Federal Funds Rate"]


def test_an_expired_saved_read_does_not_stand_in(tmp_path):
    from goldbot.news.calendar import MAX_STALE, _parse

    events = [e for e in (_parse(r) for r in _future_rows()) if e]
    store = _store_with(tmp_path, events, saved_at=1_000.0)
    assert store.load(now=1_000.0 + MAX_STALE + 1) == []


def test_a_saved_week_that_is_over_does_not_stand_in(tmp_path):
    """Serving last week's schedule as this week's is worse than saying nothing.

    The store is *fresh* here (saved just now), so only the "every event is in
    the past" rule can reject it — not the age rule. That is the rule this test
    exists to pin: a fallback may not describe a week that has already ended.
    """
    from goldbot.news.calendar import _parse

    rows = _future_rows()
    rows[0]["date"] = "2020-01-02T14:00:00-04:00"  # a week long gone
    events = [e for e in (_parse(r) for r in rows) if e]
    now = time.time()
    store = _store_with(tmp_path, events, saved_at=now)
    assert store.load(now=now) == [], "all events are in the past"


def test_a_corrupt_store_is_a_no_op(tmp_path):
    from goldbot.news.calendar import CalendarStore

    path = tmp_path / "calendar.json"
    path.write_text("{not json", encoding="utf-8")
    assert CalendarStore(path).load() == []
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert CalendarStore(path).load() == []


def test_a_stale_read_does_not_withhold_the_idea(monkeypatch):
    """The point of the fix: a blocked fetch is not an unknown window."""
    from goldbot.news import window as window_mod

    class _Stale:
        ok = False
        error = "HTTP 429"
        stale = True
        usable = True

        def high_impact(self):
            return []

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Stale(), _Rss()))
    out = window_mod.digest(now=1000.0)
    assert out["blackout"]["checked"] is True
    assert out["source"]["calendar_stale"] is True
    assert out["source"]["calendar_usable"] is True


def test_nothing_saved_and_nothing_fetched_still_withholds(monkeypatch):
    """The fail-closed rule survives: no read at all is still an unknown."""
    from goldbot.news import window as window_mod

    class _Dead:
        ok = False
        error = "HTTP 429"
        stale = False
        usable = False

        def high_impact(self):
            return []

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Dead(), _Rss()))
    out = window_mod.digest(now=1000.0)
    assert out["blackout"]["checked"] is False


def test_the_news_card_names_a_saved_read_as_stale(monkeypatch):
    class _Stale:
        ok = False
        error = "HTTP 429"
        stale = True
        usable = True

        def high_impact(self):
            return []

    monkeypatch.setattr("goldbot.news.window._feeds", lambda: (_Stale(), _Rss()))
    card = format_news(digest(now=1000.0))
    assert "بيانات قديمة" in card
    assert "تعذّر فحص التقويم" not in card, "the read is stale, not absent"
