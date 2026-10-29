"""The blackout window and the combined news digest.

A blackout is a stated rule, not a prediction: no *new* idea is offered in the
minutes around a high-impact release, because spread widens and the first move
is often a fake. Existing ideas are not cancelled — they are flagged, because a
stop already placed does not care about the calendar.

Where the rule is enforced: the flag is computed here and read by the one
predicate ``telegram.format.blackout_blocks_idea``, which *both* the automatic
push and the card a trader asks for consult. The suppression therefore lives at
the presentation boundary rather than in ``build_plan``, for two reasons: the
backtest builds plans with no calendar at all, so gating construction would make
a historical study calendar-aware in a way it cannot support; and the raw JSON
(``goldbot analyse --json``) is deliberately the analyst's unfiltered read, not a
card a trader acts on. What a trader sees is the card, and the card honours the
rule.

The predicate **fails closed**. When the fetch fails this function forces
``active=False`` — an unread window is not an *open* one — and sets
``checked=False``, which the predicate reads as "withhold anyway". An unknown
window is not a clear one, so the safe answer to "we could not check" is to
withhold the idea, never to proceed as though the calendar had been read.
"""

from __future__ import annotations

import time

from ..config import default_calendar_store
from .calendar import CalendarFeed, CalendarStore, Event
from .macro import macro_context
from .rss import RssFeed

# Default blackout: 15 minutes before, 15 after.
BEFORE_MINUTES = 15
AFTER_MINUTES = 15

# One feed per process. The cache and the 429 back-off only work if the same
# instance is reused; building a fresh feed per call is what turned a single
# rate-limit into a permanent one, because every call retried from scratch.
_FEEDS: tuple[CalendarFeed, RssFeed] | None = None


def _feeds() -> tuple[CalendarFeed, RssFeed]:
    global _FEEDS
    if _FEEDS is None:
        _FEEDS = (CalendarFeed(store=CalendarStore(default_calendar_store())), RssFeed())
    return _FEEDS


def blackout(
    events: list[Event],
    now: float | None = None,
    before: int = BEFORE_MINUTES,
    after: int = AFTER_MINUTES,
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
                {
                    "title": event.title,
                    "country": event.country,
                    "minutes": round(delta_min, 1),
                    "impact": event.impact,
                }
            )
    imminent.sort(key=lambda e: abs(e["minutes"]))
    return {
        "active": active,
        "window_minutes": {"before": before, "after": after},
        "imminent": imminent,
        "reason": "نافذة خبر عالي التأثير" if active else "لا يوجد خبر عالي التأثير قريب",
    }


def digest(now: float | None = None, with_macro: bool = True) -> dict:
    """Assemble the full news picture: schedule, tape, blackout, and macro.

    Read-only and keyless. Every fetch failure degrades to an empty list, but it
    is *reported* in ``source`` so a rate-limited calendar cannot masquerade as a
    quiet one — the caller can always tell "no news" from "no feed" by the flags
    and the counts.

    The macro leg is context, not news, and is included here because every card
    already receives this dict: it carries which way the dollar and the real
    yield lean, so a plan can be shown against the wind it is fighting. It is
    never allowed to change a direction — see :mod:`goldbot.news.macro`.
    ``with_macro=False`` omits it entirely for a purely price-based read.
    """
    now = now if now is not None else time.time()
    calendar, rss = _feeds()

    high = calendar.high_impact()
    upcoming = sorted([e for e in high if e.time >= now], key=lambda e: e.time)[:6]
    released = sorted(
        [e for e in high if e.time < now and e.actual is not None],
        key=lambda e: e.time,
        reverse=True,
    )[:5]

    window = blackout(high, now)
    if not calendar.usable:
        # Nothing to reason about at all — no live read and no saved one. Every
        # card reads ``reason``, so the honesty has to live here rather than in
        # each formatter. An unchecked calendar is not a quiet one.
        window = {
            **window,
            "active": False,
            "checked": False,
            "reason": f"تعذّر فحص التقويم: {calendar.error or 'غير متاح'}",
        }
    else:
        # A usable calendar — either a fresh read or a saved one standing in for
        # a blocked fetch. The window is *known*, so the idea is not withheld on
        # a transient block. A stale read is named as such: the trader sees the
        # read is old rather than being told it is current.
        window = {**window, "checked": True}
        if calendar.stale:
            window["reason"] = f"{window['reason']} (بيانات قديمة)"

    macro = macro_context(now) if with_macro else {}

    return {
        "generated_at": int(now),
        "blackout": window,
        "macro": macro,
        "upcoming": [
            {
                "title": e.title,
                "country": e.country,
                "impact": e.impact,
                "time": e.time,
                "in_minutes": round((e.time - now) / 60.0, 1),
                "forecast": e.forecast,
                "previous": e.previous,
            }
            for e in upcoming
        ],
        "released": [
            {**e.gold_read(), "in_minutes": round((e.time - now) / 60.0, 1)} for e in released
        ],
        "headlines": [h.as_dict() for h in rss.relevant(6)],
        "source": {
            "calendar_ok": calendar.ok,
            "calendar_error": calendar.error,
            "calendar_stale": calendar.stale,
            "calendar_usable": calendar.usable,
            "rss_ok": rss.ok,
            "rss_error": rss.error,
            "rss_stale": rss.stale,
            "macro_ok": bool(macro.get("checked")) and not macro.get("error"),
            "macro_error": macro.get("error", ""),
            "macro_stale": bool(macro.get("stale")),
        },
        "counts": {
            "high_impact_this_week": len(high),
            "upcoming": len(upcoming),
            "released": len(released),
        },
    }
