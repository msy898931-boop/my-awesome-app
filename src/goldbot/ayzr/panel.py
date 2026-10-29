"""AYZR's Telegram panel — the one seam between AYZR and the bot's UI.

Everything else in AYZR is self-contained; this module is the only thing the
main bot's dispatcher calls. It exposes a single function, :func:`handle`, that
takes a callback string in AYZR's own namespace (``ayzr:...``) and returns the
text to show. The bot never reaches into AYZR's internals, and AYZR never
reaches into the bot's — the coupling is one function, in one direction.

The namespace is prefixed ``ayzr:`` on purpose: the bot's dispatcher routes by
prefix, so an AYZR button can never collide with a bot command, and adding AYZR
screens later cannot shadow ``plan`` or ``news``.

AYZR runs on its own AI budget here, so pressing its button cannot spend the
bot's daily allowance.
"""

from __future__ import annotations

from .cards import format_decision, format_report
from .config import AyzrSettings
from .engine import Engine

AYZR_MENU: dict = {
    "inline_keyboard": [
        [
            {"text": "🤖 تحليل AYZR", "callback_data": "ayzr:analyse"},
            {"text": "📒 دفتر النتائج", "callback_data": "ayzr:report"},
        ],
        [{"text": "🔄 تحديث الدفتر", "callback_data": "ayzr:resolve"}],
    ]
}


def handle(data: str, engine: Engine | None = None) -> str:
    """Render one AYZR screen for a callback in the ``ayzr:`` namespace.

    ``engine`` is injectable for tests; in production a fresh one is built from
    the environment, which is cheap (it holds no state until it runs).
    """
    engine = engine or Engine(AyzrSettings())
    action = data.split(":", 1)[1] if ":" in data else "analyse"

    if action == "report":
        return format_report(engine.report())

    if action == "resolve":
        n = engine.resolve_only()
        return format_report(engine.report()) + f"\n\n_حُسمت {n} إشارة جديدة من الشريط._"

    # Default: a full AYZR pass — read, ask the model, validate, journal.
    run = engine.run(with_ai=True)
    if not run.read.ok:
        detail = "؛ ".join(run.read.errors[:3]) or run.error or "لا قراءة"
        return f"⚠️ *AYZR:* تعذّر قراءة السوق.\n_{detail}_"
    return format_decision(run.read, run.decision)


__all__ = ["AYZR_MENU", "handle"]
