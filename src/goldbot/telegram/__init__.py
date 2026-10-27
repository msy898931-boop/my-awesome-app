"""Telegram surface — the reader-facing half of the bot.

It polls the engine on a schedule, formats what it finds into a card a trader
can act on in one glance, and answers a few commands. It is a *reader*, not a
decider: it never places an order, and every card says so.

Two deliberate properties, both about not being annoying:

  * **No spam on start.** The first poll seeds state with everything already on
    the board and sends nothing, so a restart never replays the backlog.
  * **State is persisted.** A restart resumes instead of re-alerting.
"""

from .client import Telegram
from .format import format_analysis, format_plan, format_status
from .bot import Bot

__all__ = ["Telegram", "format_analysis", "format_plan", "format_status", "Bot"]
