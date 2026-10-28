"""News engine — the calendar, the blackout window, the gold read, the macro.

Four things a gold trader needs before deciding, assembled from keyless public
sources:

  * the **blackout window** — is a high-impact release printing, or about to?
  * the **schedule** — what high-impact events are due, and how soon?
  * the **tape** — what just printed, and which way it mechanically leans for gold?
  * the **macro** — which way the dollar and the real yield are leaning, stated
    as context, never as a signal (see :mod:`goldbot.news.macro`).

Honesty rules, carried deliberately from the reference project:

  * A release with no forecast and no previous has no baseline, so no direction
    is asserted — the record says so.
  * The gold link is only asserted for a US release, because only the dollar
    has a direct, mechanical link to gold. Elsewhere it is labelled indirect.
  * The wording is "pressure" / "support", never "will fall" / "will rise".
  * An absent feed yields empty lists, never a fabricated event.
"""

from .calendar import CalendarFeed, Event
from .macro import MacroContext, alignment, combine_votes, macro_context
from .rss import Headline, RssFeed
from .window import blackout, digest

__all__ = [
    "CalendarFeed",
    "Event",
    "RssFeed",
    "Headline",
    "blackout",
    "digest",
    "MacroContext",
    "macro_context",
    "combine_votes",
    "alignment",
]
