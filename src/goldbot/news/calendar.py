"""Economic calendar via the Forex Factory mirror (keyless).

The mirror carries *forecast* and *previous* — never a released value. That is
enough to warn before a release, but not to say what happened. The mechanical
read here is therefore a comparison of the released value (when a richer feed
supplies it) against forecast, falling back to previous, and always naming
which baseline it used.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..net import open_url

FEED_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
USER_AGENT = "gold-smc-ict-bot/0.1"

# Back-off after a failed fetch. A source that answered "too many requests"
# should not be hit again immediately; this is what keeps a 429 from becoming
# permanent. ``RETRY_AFTER_DEFAULT`` covers a 429 that omits the header.
NEGATIVE_TTL = 300.0
RETRY_AFTER_DEFAULT = 120.0

# How long a saved read may stand in for a blocked source. The feed is the
# *current week* and the source rate-limits by IP, so one blocked fetch used to
# cost every card until the next success — and because the blackout fails
# closed, that meant no ideas at all. A saved read is a fallback, not an
# archive: it is trusted only while it is recent *and* still holds an event
# ahead of now. Past that it describes a week that has already happened, which
# is not an answer, and the window goes back to being unknown.
MAX_STALE = 3 * 24 * 3600.0

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
    forecast: float | None = None
    previous: float | None = None
    actual: float | None = None
    unit: str = ""

    def gold_read(self) -> dict:
        """The mechanical lean for gold, and the baseline it was judged against.

        A US event drives the dollar directly; a stronger dollar pressures
        gold, so the sign flips. Non-US events are labelled indirect and carry
        no directional claim.
        """
        out = {
            "title": self.title,
            "country": self.country,
            "impact": self.impact,
            "actual": self.actual,
            "forecast": self.forecast,
            "previous": self.previous,
        }
        if self.actual is None:
            out.update({"read": "pending", "baseline": None, "note": "لم يصدر بعد"})
            return out
        baseline_name, baseline = ("forecast", self.forecast)
        if baseline is None:
            baseline_name, baseline = ("previous", self.previous)
        if baseline is None:
            out.update(
                {
                    "read": "no_baseline",
                    "baseline": None,
                    "note": "لا يوجد توقّع ولا سابق — لا يُستنتج اتجاه",
                }
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
                {
                    "read": "direct",
                    "baseline": baseline_name,
                    "delta": round(surprise, 3),
                    "gold": lean,
                    "note": "دولار أقوى/أضعف من المتوقع",
                }
            )
        else:
            out.update(
                {
                    "read": "indirect",
                    "baseline": baseline_name,
                    "delta": round(surprise, 3),
                    "note": "أثر غير مباشر على الذهب",
                }
            )
        return out

    def as_dict(self) -> dict:
        """The event as plain data, for the on-disk store."""
        return {
            "title": self.title,
            "country": self.country,
            "impact": self.impact,
            "time": self.time,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
            "unit": self.unit,
        }

    @classmethod
    def from_dict(cls, row: dict) -> Event | None:
        """Rebuild an event from the store, or ``None`` when it is unusable.

        A malformed row is dropped rather than repaired: a calendar that cannot
        be trusted is worse than one that is honestly absent.
        """
        try:
            return cls(
                title=str(row["title"]).strip(),
                country=str(row["country"]).strip(),
                impact=str(row["impact"]).strip().lower(),
                time=int(row["time"]),
                forecast=_to_float(row.get("forecast")),
                previous=_to_float(row.get("previous")),
                actual=_to_float(row.get("actual")),
                unit=str(row.get("unit") or ""),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _higher_is_stronger(title: str) -> bool:
    low = title.lower()
    for key, value in HIGHER_IS_STRONGER.items():
        if key in low:
            return value
    return True


def _to_float(value) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(str(value).replace("%", "").replace(",", "").strip())
    except ValueError:
        return None


def _to_epoch(raw) -> int | None:
    """Parse the mirror's ISO timestamp into Unix seconds."""
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(text).timestamp())
    except ValueError:
        return None


class CalendarStore:
    """The last good calendar read, on disk, so a blocked fetch is survivable.

    The source rate-limits by IP and the bot runs on a shared host, so a 429 is
    routine rather than exceptional. Without a saved read, one blocked fetch
    meant "could not check the calendar", which the blackout correctly turns
    into "withhold the idea" — so a transient block on the feed produced no
    trade ideas at all, which is the opposite of the intent. The saved read lets
    a blocked fetch degrade to a *stale but usable* calendar instead.

    It is deliberately not an archive: :meth:`load` refuses a read that is too
    old or that no longer has an event ahead of it, so the fallback can only
    ever describe the week we are actually in. Past that, "we could not check"
    is the truthful answer and the window stays unknown.

    Plain JSON, written atomically, safe to delete: a missing store simply means
    no fallback, which is exactly the old behaviour.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def save(self, events: list[Event], now: float | None = None) -> None:
        """Persist a good read. Best-effort: a store that cannot be written is
        not worth failing a card over."""
        stamp = int(now if now is not None else time.time())
        payload = json.dumps(
            {"saved_at": stamp, "events": [e.as_dict() for e in events]},
            ensure_ascii=False,
        )
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self.path)
            except OSError:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        except OSError:
            pass

    def load(self, now: float | None = None) -> list[Event]:
        """The saved read, if it is still fit to stand in for a live one.

        Empty when there is no store, it cannot be read, it is older than
        :data:`MAX_STALE`, or every event it holds is already in the past — the
        last of these is what stops a fallback from serving last week's schedule
        as if it were this week's.
        """
        now = now if now is not None else time.time()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(raw, dict):
            return []
        try:
            saved_at = float(raw.get("saved_at") or 0)
        except (TypeError, ValueError):
            return []
        if saved_at <= 0 or now - saved_at > MAX_STALE:
            return []
        events = [e for e in (Event.from_dict(r) for r in raw.get("events") or []) if e]
        if not any(e.time >= now for e in events):
            # The saved week is behind us; it cannot say what is coming.
            return []
        return events


class CalendarFeed:
    """Fetches the week's calendar, best-effort.

    "Best-effort" must not mean "silently empty". A dead feed yields no events,
    but the caller has to be able to tell that apart from a genuinely quiet week
    — otherwise a rate-limited fetch reads as "no news today", which is how a
    scheduled FOMC release went missing while the card called the window calm.
    ``ok`` and ``error`` therefore report the last attempt, and a failure keeps
    the previous good read (marked ``stale``) instead of dropping the calendar.
    """

    def __init__(
        self,
        url: str = FEED_URL,
        timeout: float = 15.0,
        store: CalendarStore | None = None,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.store = store
        self._cache: tuple[float, list[Event]] = (0.0, [])
        self._ok = False
        self._error = ""
        self._retry_at = 0.0
        if store is not None:
            # A saved read is the starting point, so a process that begins life
            # behind a 429 still has a calendar to reason about. The cache stamp
            # is left at 0 so the first ``events()`` still attempts a live fetch:
            # the saved read is a fallback, never a reason to skip the source.
            # It is not a successful fetch either — ``ok`` stays False and
            # ``stale`` turns True, so the cards still say the live read failed.
            self._cache = (0.0, store.load())

    @property
    def ok(self) -> bool:
        """Whether the most recent fetch reached the source."""
        return self._ok

    @property
    def error(self) -> str:
        """Why the last fetch failed, or an empty string when it did not."""
        return self._error

    @property
    def stale(self) -> bool:
        """Serving an older read because the newest attempt failed."""
        return not self._ok and bool(self._cache[1])

    @property
    def usable(self) -> bool:
        """Whether the events in hand can be reasoned about at all.

        True after any successful fetch, and also when a saved read is standing
        in for one. This is the distinction the blackout cares about: a stale
        read is *usable* (the window is known, if perhaps out of date) whereas no
        read at all is not, and only the latter should withhold an idea.
        """
        return self._ok or bool(self._cache[1])

    def events(self, ttl: float = 900.0) -> list[Event]:
        now = time.time()
        if now - self._cache[0] < ttl and self._cache[1]:
            return self._cache[1]
        rows, error = self._fetch()
        if error:
            self._ok = False
            self._error = error
            # Back off before trying again. Hammering a source that just said
            # "too many requests" is what keeps it saying that.
            self._retry_at = max(self._retry_at, now + NEGATIVE_TTL)
            return self._cache[1]
        events = [e for e in (_parse(r) for r in rows) if e is not None]
        self._cache = (now, events)
        self._ok = True
        self._error = ""
        if self.store is not None:
            # Save the raw week, not just the high-impact subset: the digest
            # also renders upcoming and released events.
            self.store.save(events, now)
        return self._cache[1]

    def _fetch(self) -> tuple[list[dict], str]:
        """One attempt. Returns the rows and an error string ('' on success)."""
        if time.time() < self._retry_at:
            return [], f"backing off ({int(self._retry_at - time.time())}s)"
        req = urllib.request.Request(self.url, headers={"User-Agent": USER_AGENT})
        try:
            with open_url(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return [], f"HTTP {resp.status}"
                data = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            # HTTPError subclasses URLError, so it must be caught first. A 429
            # carries Retry-After, and ignoring it is how the feed stays closed.
            wait = _retry_after(exc.headers) or (RETRY_AFTER_DEFAULT if exc.code == 429 else 0.0)
            if wait:
                self._retry_at = time.time() + wait
            return [], f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return [], type(exc).__name__
        if not isinstance(data, list):
            return [], "unexpected payload"
        return data, ""

    def high_impact(self) -> list[Event]:
        return [e for e in self.events() if e.impact.lower() in HIGH_IMPACT]


def _retry_after(headers) -> float:
    """Seconds to wait, from a ``Retry-After`` header. 0 when absent or bad."""
    raw = (headers or {}).get("Retry-After", "")
    try:
        return max(0.0, min(float(raw), 3600.0))
    except (TypeError, ValueError):
        return 0.0


def _parse(row: dict) -> Event | None:
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
