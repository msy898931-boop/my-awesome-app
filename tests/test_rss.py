"""Tests for the headline feeds: honesty, hardening, and parsing.

The RSS layer used to fail silently — a dead feed and a quiet news day looked
identical, which is the exact bug that was fixed for the calendar and then left
in place here. These tests pin the fix and the XML defences that keep an
untrusted feed from becoming an attack.
"""

from __future__ import annotations

import urllib.error

from goldbot.news.rss import MAX_FEED_BYTES, RssFeed, _clean, _epoch, _text

# ── canned feeds ─────────────────────────────────────────────────────────

GOOD_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item><title>Gold holds $4000 as Fed holds</title>
        <link>https://x/1</link>
        <pubDate>Wed, 01 Oct 2025 12:00:00 GMT</pubDate></item>
  <item><title>Dollar slips on weak payrolls</title>
        <link>https://x/2</link>
        <pubDate>Wed, 01 Oct 2025 11:00:00 GMT</pubDate></item>
</channel></rss>"""

EMPTY_XML = b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'


class _Resp:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self.status = status

    def read(self, n: int = -1) -> bytes:
        return self._body if n < 0 else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _feed_returning(bodies, **kw):
    """An RssFeed whose urlopen yields the given bodies in order."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        body = bodies[calls["n"] % len(bodies)]
        calls["n"] += 1
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, _Resp) else _Resp(body)

    feed = RssFeed(feeds=[("S", "https://x/feed")], **kw)
    return feed, fake_urlopen, calls


# ── health: an empty read must not look like a quiet day ─────────────────


def test_all_feeds_failing_reports_not_ok(monkeypatch):
    feed, fake, _ = _feed_returning([urllib.error.URLError("down")])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert feed.ok is False
    assert feed.error, "a total failure must carry a reason"


def test_one_dead_feed_does_not_poison_a_good_one(monkeypatch):
    bodies = [urllib.error.URLError("down"), GOOD_XML]
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        body = bodies[calls["n"]]
        calls["n"] += 1
        if isinstance(body, Exception):
            raise body
        return _Resp(body)

    feed = RssFeed(feeds=[("Dead", "https://x/dead"), ("Live", "https://x/live")])
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    items = feed.headlines()
    assert len(items) == 2
    assert feed.ok is True, "one live feed is a success"


def test_a_successful_read_is_ok_with_no_error(monkeypatch):
    feed, fake, _ = _feed_returning([GOOD_XML])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert len(feed.headlines()) == 2
    assert feed.ok is True
    assert feed.error == ""
    assert feed.stale is False


def test_a_total_failure_keeps_the_last_good_read_as_stale(monkeypatch):
    """The previous headlines survive a later outage, flagged as stale."""
    state = {"body": GOOD_XML}

    def fake_urlopen(req, timeout=0):
        if isinstance(state["body"], Exception):
            raise state["body"]
        return _Resp(state["body"])

    feed = RssFeed(feeds=[("S", "https://x/feed")], timeout=1.0)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert len(feed.headlines()) == 2

    state["body"] = urllib.error.URLError("down")
    feed._cache = (0.0, feed._cache[1])  # expire the ttl, keep the read
    feed._retry_at = 0.0
    assert len(feed.headlines()) == 2, "the last good read is served"
    assert feed.ok is False
    assert feed.stale is True


def test_a_failed_fetch_backs_off(monkeypatch):
    """A source that just refused us is not hit again immediately."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.URLError("down")

    feed = RssFeed(feeds=[("S", "https://x/feed")], timeout=1.0)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    feed.headlines()
    assert calls["n"] == 1
    feed.headlines()  # inside the back-off window
    assert calls["n"] == 1, "the retry must be suppressed"


# ── hardening: the untrusted body ────────────────────────────────────────


def test_a_doctype_is_refused_before_parsing(monkeypatch):
    """A DOCTYPE is the entity-expansion vector; no real feed carries one."""
    bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "x">]><rss><item/></rss>'
    feed, fake, _ = _feed_returning([bomb])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert feed.ok is False
    assert "doctype" in feed.error.lower()


def test_a_bom_does_not_smuggle_a_doctype_past_the_guard(monkeypatch):
    """A leading BOM must not defeat the DOCTYPE scan.

    An earlier anchored-regex version missed ``BOM + DOCTYPE``, and ET happily
    expanded the entity. The scan is a substring search precisely so no
    prologue trick can hide the declaration.
    """
    bomb = b'\xef\xbb\xbf<!DOCTYPE r [<!ENTITY a "x">]><rss><item><title>&a;</title></item></rss>'
    feed, fake, _ = _feed_returning([bomb])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert "doctype" in feed.error.lower()


def test_a_legitimate_bom_document_is_still_parsed(monkeypatch):
    feed, fake, _ = _feed_returning([b"\xef\xbb\xbf" + GOOD_XML])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert len(feed.headlines()) == 2


def test_an_oversized_body_is_refused(monkeypatch):
    huge = b"<?xml version='1.0'?><rss>" + b" " * MAX_FEED_BYTES + b"</rss>"
    feed, fake, _ = _feed_returning([huge])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert "bytes" in feed.error


def test_malformed_xml_is_reported_not_swallowed(monkeypatch):
    feed, fake, _ = _feed_returning([b"<rss><channel><item>unclosed"])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert feed.ok is False
    assert feed.error


def test_a_non_200_is_reported(monkeypatch):
    feed, fake, _ = _feed_returning([_Resp(b"", status=503)])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert feed.headlines() == []
    assert "503" in feed.error


# ── parsing ──────────────────────────────────────────────────────────────


def test_atom_entries_are_read_too(monkeypatch):
    atom = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><title>Gold up</title><link href="https://x"/>
        <updated>Wed, 01 Oct 2025 12:00:00 GMT</updated></entry>
    </feed>"""
    feed, fake, _ = _feed_returning([atom])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    items = feed.headlines()
    assert [h.title for h in items] == ["Gold up"]


def test_duplicate_titles_are_collapsed(monkeypatch):
    dup = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Gold up</title></item>
      <item><title>Gold up</title></item>
      <item><title>Gold down</title></item>
    </channel></rss>"""
    feed, fake, _ = _feed_returning([dup])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    titles = [h.title for h in feed.headlines()]
    assert titles.count("Gold up") == 1 and "Gold down" in titles


def test_items_without_a_title_are_skipped(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><link>https://x/1</link></item>
      <item><title>Real</title></item>
    </channel></rss>"""
    feed, fake, _ = _feed_returning([xml])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    assert [h.title for h in feed.headlines()] == ["Real"]


def test_relevant_keeps_only_gold_moving_terms(monkeypatch):
    xml = b"""<?xml version="1.0"?><rss><channel>
      <item><title>Gold hits record high</title></item>
      <item><title>Local football results</title></item>
      <item><title>Fed holds rates steady</title></item>
    </channel></rss>"""
    feed, fake, _ = _feed_returning([xml])
    monkeypatch.setattr("urllib.request.urlopen", fake)
    titles = [h.title for h in feed.relevant()]
    assert "Gold hits record high" in titles
    assert "Fed holds rates steady" in titles
    assert "Local football results" not in titles


# ── the small helpers ────────────────────────────────────────────────────


def test_clean_strips_tags_and_collapses_space():
    assert _clean("  Gold   <b>up</b>\n today ") == "Gold up today"


def test_epoch_parses_rfc822_and_tolerates_junk():
    assert _epoch("Wed, 01 Oct 2025 12:00:00 GMT") > 0
    assert _epoch("not a date") == 0
    assert _epoch("") == 0


def test_text_reads_a_child_case_insensitively():
    import xml.etree.ElementTree as ET

    node = ET.fromstring("<item><PubDate>x</PubDate></item>")
    assert _text(node, "pubdate") == "x"
    assert _text(node, "missing") == ""
