"""When to speak up about news, unprompted.

The bot used to be entirely reactive: the calendar, the window, and the
schedule only reached the trader if they pressed a button. That is the wrong
default for the one thing a trader cannot check for themselves — a release is
coming in half an hour, and the blackout that withholds new ideas opens *at*
the print, which is too late to be told.

This module holds the decision, as a pure function, so it can be tested without
a clock or a network: given the news picture, a lead time, and what has already
been announced, which events deserve a heads-up now?

Two honesty rules carry over from the rest of the news layer:

  * an unread calendar announces **nothing**. "No feed" is not "no news", and a
    fabricated alert is worse than a missed one — the whole point of the alert
    is that its silence means something.
  * an event is announced **once**. The key is the event's own identity
    (country, title, and scheduled time), so a restart, a repeated read, or a
    wobbly minute counter cannot turn one release into a stream of alerts.
"""

from __future__ import annotations

import time

# An announced event is forgotten after this long. Long enough that the same
# weekly release cannot re-fire on the next cycle, short enough that the state
# file does not grow without bound.
FORGET_AFTER = 3 * 24 * 3600.0


def alert_key(event: dict) -> str:
    """The identity of a scheduled event: who, what, and *when* it prints.

    The scheduled time is what makes it an identity rather than a category — the
    same "CPI" next month is a different event and must be allowed to announce
    itself. Title and country are included so two releases at the same second
    stay distinct.
    """
    return f"{event.get('country')}|{event.get('title')}|{event.get('time')}"


def due_news_alerts(
    news: dict | None,
    lead_minutes: float,
    announced: dict | None = None,
    now: float | None = None,
) -> list[dict]:
    """The high-impact events inside the lead window that are not yet announced.

    ``lead_minutes`` <= 0 disables the heads-up entirely. The window is
    ``0 <= minutes <= lead``: an event already printing is the blackout's job,
    not the alert's, and the blackout card says so.
    """
    if lead_minutes <= 0 or not news:
        return []
    source = news.get("source") or {}
    if not source.get("calendar_ok", True):
        # Never announce off a calendar we could not read. A missed heads-up is
        # recoverable; an invented one is a claim about the world.
        return []
    seen = announced or {}
    out: list[dict] = []
    for event in news.get("upcoming") or []:
        minutes = event.get("in_minutes")
        if minutes is None:
            continue
        if not (0 <= minutes <= lead_minutes):
            continue
        if alert_key(event) in seen:
            continue
        out.append(event)
    # Nearest first: if two releases are due, the trader hears about the sooner.
    out.sort(key=lambda e: e.get("in_minutes") or 0)
    return out


def prune(announced: dict, now: float | None = None) -> dict:
    """Drop announced keys whose event is long past, so the state stays small."""
    now = now if now is not None else time.time()
    return {k: v for k, v in announced.items() if (v or 0) > now - FORGET_AFTER}
