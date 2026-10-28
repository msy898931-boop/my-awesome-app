"""Headline feeds — keyless RSS from outlets that move gold.

Used to give the AI layer a sense of the current narrative, never to drive a
level. A headline is context, and is labelled as such; the numbers that matter
are computed from candles.

The same honesty rule the calendar follows applies here: an empty headline list
must be distinguishable from a dead feed. ``ok`` and ``error`` report the last
aggregate attempt, and a total failure keeps the previous good read (marked
``stale``) rather than replacing it with silence.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET  # nosec B405
from dataclasses import dataclass

from ..net import open_url

USER_AGENT = "gold-smc-ict-bot/0.1"

# A feed body larger than this is refused before parsing. Feeds are tens of
# kilobytes; anything past this is a hostile or broken response, and reading it
# into memory to find that out is the denial of service.
MAX_FEED_BYTES = 2_000_000

# Back-off after a failed attempt, so a source that just refused us is not
# hammered into refusing forever. Mirrors ``calendar.NEGATIVE_TTL``.
NEGATIVE_TTL = 300.0

# A DOCTYPE or an ENTITY declaration is the vector for entity-expansion
# attacks (a "billion laughs" or an external entity read), and ET expands them
# by default. None of the real feeds carry one — checked against all three — so
# a document that does is refused rather than parsed. Inside XML text a literal
# ``<`` must be escaped, so ``<!DOCTYPE`` can only ever be a real declaration,
# never a headline; a plain substring scan is therefore both safe and immune to
# a leading BOM or whitespace trick. This is the stdlib-only defence: no
# ``defusedxml`` dependency.
_FORBIDDEN_PROLOGUE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)

# Labels must name the source that actually answers. Two were wrong: the
# "Reuters" URL is WSJ's feed, and news_301 is Investing's *crypto* stream —
# which is the wrong asset entirely for a gold bot, so it was crowding out
# commodity headlines with bitcoin ones.
FEEDS = (
    ("WSJ Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Investing Commodities", "https://www.investing.com/rss/news_11.rss"),
    ("Investing Forex", "https://www.investing.com/rss/news_1.rss"),
)

# Terms that make a headline plausibly gold-relevant.
GOLD_TERMS = (
    "gold",
    "xauusd",
    "bullion",
    "fed",
    "fomc",
    "rate",
    "inflation",
    "cpi",
    "dollar",
    "treasury",
    "yield",
    "geopolit",
    "war",
    "sanction",
    "opec",
    "recession",
    "payroll",
    "powell",
    "ecb",
    "boj",
    "stimulus",
)


@dataclass
class Headline:
    title: str
    source: str
    link: str = ""
    published: int = 0

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "source": self.source,
            "link": self.link,
            "published": self.published,
        }


class RssFeed:
    """Aggregates the configured feeds, best-effort, with a short cache.

    "Best-effort" must not mean "silently empty". The feeds are independent, so
    one dead outlet does not sink the digest; but if *every* feed fails, that is
    reported rather than shown as a quiet news day. The last good read is kept
    and marked ``stale``, exactly as the calendar does.
    """

    def __init__(self, feeds=FEEDS, timeout: float = 12.0) -> None:
        self.feeds = feeds
        self.timeout = timeout
        self._cache: tuple[float, list[Headline]] = (0.0, [])
        self._ok = False
        self._error = ""
        self._retry_at = 0.0

    @property
    def ok(self) -> bool:
        """Whether the most recent attempt reached at least one feed."""
        return self._ok

    @property
    def error(self) -> str:
        """Why the last attempt failed, or an empty string when it did not."""
        return self._error

    @property
    def stale(self) -> bool:
        """Serving an older read because the newest attempt failed entirely."""
        return not self._ok and bool(self._cache[1])

    def headlines(self, ttl: float = 600.0) -> list[Headline]:
        now = time.time()
        if now - self._cache[0] < ttl and self._cache[1]:
            return self._cache[1]
        if now < self._retry_at:
            return self._cache[1]

        out: list[Headline] = []
        failures = 0
        last_error = ""
        for source, url in self.feeds:
            items, error = self._parse(source, url)
            out.extend(items)
            if error:
                failures += 1
                last_error = error

        # All feeds down: report it, keep the previous read, and back off. One
        # feed down is normal and must not poison the digest.
        if self.feeds and failures == len(self.feeds):
            self._ok = False
            self._error = last_error or "all feeds failed"
            self._retry_at = now + NEGATIVE_TTL
            return self._cache[1]

        seen: set[str] = set()
        unique: list[Headline] = []
        for item in sorted(out, key=lambda h: h.published, reverse=True):
            key = item.title.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        self._cache = (now, unique)
        self._ok = True
        self._error = ""
        return unique

    def relevant(self, limit: int = 8) -> list[Headline]:
        """Headlines whose text mentions something that moves gold."""
        out: list[Headline] = []
        for item in self.headlines():
            low = item.title.lower()
            if any(term in low for term in GOLD_TERMS):
                out.append(item)
            if len(out) >= limit:
                break
        return out

    def _parse(self, source: str, url: str) -> tuple[list[Headline], str]:
        """One feed. Returns the headlines and an error string ('' on success).

        The error is returned rather than swallowed so the caller can tell a
        quiet feed from a refused one. ``_parse`` itself never raises.
        """
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with open_url(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return [], f"HTTP {resp.status}"
                # Read one byte past the cap: if it is there, the body is over.
                raw = resp.read(MAX_FEED_BYTES + 1)
        except urllib.error.HTTPError as exc:
            return [], f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return [], type(exc).__name__

        if len(raw) > MAX_FEED_BYTES:
            return [], f"body over {MAX_FEED_BYTES} bytes"
        if _FORBIDDEN_PROLOGUE.search(raw):
            # A DOCTYPE/ENTITY opens the door to entity expansion; no real feed
            # needs one, so refuse rather than risk parsing an attack.
            return [], "doctype not allowed"
        try:
            # DOCTYPE/ENTITY were refused above, so no external entity can
            # expand here; the parse is on bytes we already vetted.
            root = ET.fromstring(raw)  # nosec B314
        except ET.ParseError:
            return [], "malformed XML"

        items: list[Headline] = []
        for node in root.iter():
            tag = node.tag.split("}")[-1].lower()
            if tag not in {"item", "entry"}:
                continue
            title = _text(node, "title")
            if not title:
                continue
            items.append(
                Headline(
                    title=_clean(title),
                    source=source,
                    link=_text(node, "link"),
                    published=_epoch(_text(node, "pubdate") or _text(node, "published")),
                )
            )
        return items[:25], ""


def _text(node, name: str) -> str:
    for child in node:
        if child.tag.split("}")[-1].lower() == name:
            return (child.text or "").strip()
    return ""


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", text)).strip()


def _epoch(raw: str) -> int:
    if not raw:
        return 0
    from email.utils import parsedate_to_datetime

    try:
        return int(parsedate_to_datetime(raw).timestamp())
    except (TypeError, ValueError):
        return 0
