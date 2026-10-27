"""News engine — the calendar, the blackout window, and the gold read.

Three things a gold trader needs before deciding, assembled from keyless public
sources:

  * the **blackout window** — is a high-impact release printing, or about to?
  * the **schedule** — what high-impact events are due, and how soon?
  * the **tape** — what just printed, and which way it mechanically leans for gold?

Honesty rules, carried deliberately from the reference project:

  * A release with no forecast and no previous has no baseline, so no direction
    is asserted — the record says so.
  * The gold link is only asserted for a US release, because only the dollar
    has a direct, mechanical link to gold. Elsewhere it is labelled indirect.
  * The wording is "pressure" / "support", never "will fall" / "will rise".
  * An absent feed yields empty lists, never a fabricated event.
"""

from .calendar import CalendarFeed, Event
from .rss import RssFeed, Headline
from .window import blackout, digest

__all__ = ["CalendarFeed", "Event", "RssFeed", "Headline", "blackout", "digest"]
