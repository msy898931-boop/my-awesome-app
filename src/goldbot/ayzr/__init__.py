"""AYZR — a separate, AI-first gold engine that lives beside the main bot.

The name is the user's. The engine is deliberately **independent**: its own
settings, its own AI key and daily quota, its own journal on its own disk path,
and its own Telegram panel. Running AYZR cannot spend the main bot's AI budget,
cannot touch its state or archive, and cannot break a card it already sends.

What AYZR *does* share is the read-only market layer — the candle providers, the
SMC/ICT arithmetic, and the news digest — because re-implementing those would be
duplication, not isolation.

The difference from the main bot is where the decision comes from. The main bot
decides with arithmetic and uses the model to narrate. AYZR asks the model to
decide, then **validates its answer against the arithmetic** before anything is
journalled. That is the whole experiment: does a model weighing the full picture
pick better trades than the rules do — and better than a coin? The journal, with
its matched random control, is what will answer it.
"""

from .cards import format_decision, format_report
from .config import AyzrSettings
from .decide import Decision, decide, validate_trade
from .engine import Engine, Run
from .journal import log_no_trade, log_signal, open_signals, read_all, resolve_open
from .read import Read, read_market
from .report import Report, build_report, compute, verdict

__all__ = [
    "AyzrSettings",
    "Decision",
    "Engine",
    "Read",
    "Report",
    "Run",
    "build_report",
    "compute",
    "decide",
    "format_decision",
    "format_report",
    "log_no_trade",
    "log_signal",
    "open_signals",
    "read_all",
    "read_market",
    "resolve_open",
    "validate_trade",
    "verdict",
]
