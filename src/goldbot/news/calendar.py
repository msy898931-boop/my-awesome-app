"""Economic calendar via the Forex Factory mirror (keyless).

The mirror carries *forecast* and *previous* — never a released value. That is
enough to warn before a release, but not to say what happened. The mechanical
read here is therefore a comparison of the released value (when a richer feed
supplies it) against forecast, falling back to previous, and always naming
which baseline it used.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "gold-smc-ict-bot/0.1"

# Which way a higher print leans for the dollar, per indicator. Jobless claims
# invert it: more claims is a weaker labour market, hence a weaker dollar.
HIGHER_IS_STRONGER = {
    "non-farm": True,
    "nonfarm": True,
    "employment change": True,
    "unemployment rate": False,
    "cpi": True,
    "inflation": True,
    "ppi": True,
    "gdp": True,
    "retail sales": True,
    "interest rate": True,
    "federal funds": True,
    "jobless claims": False,
    "core": True,
    "pmi": True,
    "ism": True,
    "consumer confidence": True,
}

# Events that move gold enough to justify a blackout.
HIGH_IMPACT = {"high"}


@dataclass
class Event:
    """One scheduled release, with its mechanical read once it prints."""

    title: str
    country: str
    impact: str
    time: int
    forecast: Optional[float] = None
    previous: Optional[float] = None
    actual: Optional[float] = None
    unit: str = ""

    def gold_read(self) -> dict:
        """The mechanical lean for gold, and the baseline it was judged against.

        A US event drives the dollar directly; a stronger dollar pressures
        gold, so the sign flips. Non-US events are labelled indirect and carry
        no directional claim.
        """
        out = {"title": self.title, "country": self.country, "impact": self.impact}
        if self.actual is None:
            out.update({"read": "pending", "baseline": None, "note": "لم يصدر بعد"})
            return out
        baseline_name, baseline = ("forecast", self.forecast)
        if baseline is None:
            baseline_name, baseline = ("previous", self.previous)
        if baseline is None:
            out.update(
                {"read": "no_baseline", "baseline": None,
                 "note": "لا يوجد توقّع ولا سابق — لا يُستنتج اتجاه"}
            )
            return out

        higher_stronger = _higher_is_stronger(self.title)
        surprise = self.actual - baseline
        dollar_strong = (surprise > 0) == higher_stronger and surprise != 0
        if surprise == 0:
            out.update({"read": "inline", "baseline": baseline_name, "delta": 0.0})
            return out

        if self.country.upper() in {"USD", "US", "UNITED STATES"}:
            lean = "ضغط على الذهب" if dollar_strong else "دعم للذهب"
            out.update(
                {"read": "direct", "baseline": baseline_name, "delta": round(surprise, 3),
                 "gold": lean, "note": "دولار أقوى/أضعف من المتوقع"}
            )
        else:
            out.update(
                {"read": "indirect", "baseline": baseline_name, "delta": round(surprise, 3),
                 "note": "أثر غير مباشر على الذهب"}
            )
        return out


def _higher_is_stronger(title: str) -> bool:
    low = title.lower()
    for key, value in HIGHER_IS_STRONGER.items():
        if key in low:
            return value
    return True


def _to_float(value) -> Optional[float]:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def _to_epoch(raw) -> Optional[int]:
    """Parse the mirror's ISO timestamp into Unix seconds."""
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


class CalendarFeed:
    """Fetches the week's calendar, best-effort. A dead feed yields no events."""

    def __init__(self, url: str = FEED_URL, timeout: float = 15.0) -> None:
        self.url = url
        self.timeout = timeout
        self._cache: tuple[float, list[Event]] = (0.0, [])

    def events(self, ttl: float = 900.0) -> list[Event]:
        now = time.time()
        if now - self._cache[0] < ttl and self._cache[1]:
            return self._cache[1]
        rows = self._fetch()
        events = [e for e in (_parse(row) for row in rows) if e is not None]
        self._cache = (now, events)
        return events

    def _fetch(self) -> list[dict]:
        req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return []
                return json.loads(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return []

    def high_impact(self) -> list[Event]:
        return [e for e in self.events() if e.impact.lower() in HIGH_IMPACT]


def _parse(row: dict) -> Optional[Event]:
    title = (row.get("title") or "").strip()
    when = _to_epoch(row.get("date"))
    if not title or when is None:
        return None
    return Event(
        title=title,
        country=(row.get("country") or "").strip(),
        impact=(row.get("impact") or "").strip().lower(),
        time=when,
        forecast=_to_float(row.get("forecast")),
        previous=_to_float(row.get("previous")),
        actual=_to_float(row.get("actual")),
    )
