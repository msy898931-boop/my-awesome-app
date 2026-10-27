"""Headline feeds — keyless RSS from outlets that move gold.

Used to give the AI layer a sense of the current narrative, never to drive a
level. A headline is context, and is labelled as such; the numbers that matter
are computed from candles.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

USER_AGENT = "gold-smc-ict-bot/0.1"

FEEDS = (
    ("Reuters Markets", "https://feeds.a.dj.com/rss/RSSMarketsMain.xml"),
    ("Investing News", "https://www.investing.com/rss/news_301.rss"),
    ("Investing Commodities", "https://www.investing.com/rss/news_11.rss"),
)

# Terms that make a headline plausibly gold-relevant.
GOLD_TERMS = (
    "gold", "xauusd", "bullion", "fed", "fomc", "rate", "inflation", "cpi",
    "dollar", "treasury", "yield", "geopolit", "war", "sanction", "opec",
    "recession", "payroll", "powell", "ecb", "boj", "stimulus",
)


@dataclass
class Headline:
    title: str
    source: str
    link: str = ""
    published: int = 0

    def as_dict(self) -> dict:
        return {"title": self.title, "source": self.source, "link": self.link,
                "published": self.published}


class RssFeed:
    """Aggregates the configured feeds, best-effort, with a short cache."""

    def __init__(self, feeds=FEEDS, timeout: float = 12.0) -> None:
        self.feeds = feeds
        self.timeout = timeout
        self._cache: tuple[float, list[Headline]] = (0.0, [])

    def headlines(self, ttl: float = 600.0) -> list[Headline]:
        now = time.time()
        if now - self._cache[0] < ttl and self._cache[1]:
            return self._cache[1]
        out: list[Headline] = []
        for source, url in self.feeds:
            out.extend(self._parse(source, url))
        seen: set[str] = set()
        unique: list[Headline] = []
        for item in sorted(out, key=lambda h: h.published, reverse=True):
            key = item.title.lower()[:80]
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        self._cache = (now, unique)
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

    def _parse(self, source: str, url: str) -> list[Headline]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return []
                raw = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            return []
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            return []

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
        return items[:25]


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
