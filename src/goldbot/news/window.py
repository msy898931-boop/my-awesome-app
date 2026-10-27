"""The blackout window and the combined news digest.

A blackout is a stated rule, not a prediction: no *new* idea is offered in the
minutes around a high-impact release, because spread widens and the first move
is often a fake. Existing ideas are not cancelled — they are flagged, because a
stop already placed does not care about the calendar.
"""

from __future__ import annotations

import time
from typing import Optional

from .calendar import CalendarFeed, Event
from .rss import RssFeed

# Default blackout: 15 minutes before, 15 after.
BEFORE_MINUTES = 15
AFTER_MINUTES = 15


def blackout(
    events: list[Event], now: Optional[float] = None,
    before: int = BEFORE_MINUTES, after: int = AFTER_MINUTES,
) -> dict:
    """Whether a high-impact release is printing now or imminent.

    Returns a window with the triggering event, minutes to its open, and a
    plain ``active`` flag. With no events it is inactive, never an error.
    """
    now = now if now is not None else time.time()
    imminent: list[dict] = []
    active = False
    for event in events:
        delta_min = (event.time - now) / 60.0
        if -after <= delta_min <= before:
            active = True
            imminent.append(
                {"title": event.title, "country": event.country,
                 "minutes": round(delta_min, 1), "impact": event.impact}
            )
    imminent.sort(key=lambda e: abs(e["minutes"]))
    return {
        "active": active,
        "window_minutes": {"before": before, "after": after},
        "imminent": imminent,
        "reason": "نافذة خبر عالي التأثير" if active else "لا يوجد خبر عالي التأثير قريب",
    }


def digest(now: Optional[float] = None) -> dict:
    """Assemble the full news picture: schedule, tape and blackout.

    Read-only and keyless. Every fetch failure degrades to an empty list and
    the digest remains a valid document — the caller can always tell "no news"
    from "no feed" by the counts.
    """
    now = now if now is not None else time.time()
    calendar = CalendarFeed()
    rss = RssFeed()

    high = calendar.high_impact()
    upcoming = sorted(
        [e for e in high if e.time >= now], key=lambda e: e.time
    )[:6]
    released = sorted(
        [e for e in high if e.time < now and e.actual is not None],
        key=lambda e: e.time, reverse=True,
    )[:5]

    return {
        "generated_at": int(now),
        "blackout": blackout(high, now),
        "upcoming": [
            {"title": e.title, "country": e.country, "impact": e.impact,
             "time": e.time, "in_minutes": round((e.time - now) / 60.0, 1),
             "forecast": e.forecast, "previous": e.previous}
            for e in upcoming
        ],
        "released": [e.gold_read() for e in released],
        "headlines": [h.as_dict() for h in rss.relevant(6)],
        "counts": {
            "high_impact_this_week": len(high),
            "upcoming": len(upcoming),
            "released": len(released),
        },
    }
