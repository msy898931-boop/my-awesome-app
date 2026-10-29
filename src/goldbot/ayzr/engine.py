"""AYZR's engine: read, ask, validate, journal — in one call.

This is the seam the rest of AYZR (the CLI and the Telegram panel) talks to.
It owns no state of its own beyond what it hands to the journal and the AI
client, so a run is a pure function of the market and the clock: same tape in,
same read out.

The order of operations is deliberate:

  1. **resolve first.** Any signal whose bars have formed is settled *before* a
     new one is read, so the journal is never holding a stale open signal while
     a fresh decision is made on the same tape.
  2. **read.** Structure, news, macro, spot — through the shared, read-only
     layers.
  3. **ask.** One model call, on AYZR's own budget, only if a key is set and the
     daily cap allows it.
  4. **validate.** The model's trade survives the arithmetic or is rejected with
     its reason.
  5. **journal.** The trade (with its coin-flip control) or the "no trade" call
     is written, so the month's denominator is complete either way.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .client import AiBudgetError, AiClient, parse_json
from .config import AyzrSettings
from .decide import Decision, decide
from .journal import log_no_trade, log_signal, resolve_open
from .prompt import SYSTEM_PROMPT, build_prompt
from .read import Read, read_market
from .report import Report, build_report


@dataclass
class Run:
    """One AYZR run: the read, the decision, and what was journalled."""

    read: Read = field(default_factory=Read)
    decision: Decision = field(default_factory=Decision)
    journalled: dict | None = None
    resolved: int = 0
    error: str = ""

    @property
    def price(self) -> float | None:
        return (self.read.spot or {}).get("price") if self.read.spot else None


class Engine:
    """Wires AYZR together. One instance per process is enough."""

    def __init__(self, settings: AyzrSettings | None = None, feed=None) -> None:
        self.settings = settings or AyzrSettings()
        self._feed = feed

    def _feed_or_build(self):
        if self._feed is not None:
            return self._feed
        from .read import _build_readonly_feed

        return _build_readonly_feed(self.settings)

    def run(self, *, with_ai: bool = True, journal: bool = True) -> Run:
        """Perform one full AYZR pass."""
        settings = self.settings
        settings.ensure_dirs()
        feed = self._feed_or_build()
        out = Run()

        # 1 — settle what has already played out.
        try:
            out.resolved = resolve_open(settings, feed)
        except Exception as exc:  # resolution must never block a new read
            out.error = f"resolve: {type(exc).__name__}: {exc}"

        # 2 — read the market.
        out.read = read_market(settings, feed=feed)
        if not out.read.ok:
            out.error = out.error or "; ".join(out.read.errors) or "لا قراءة"
            return out

        # 3 — ask the model (on AYZR's budget), if allowed.
        decision = Decision()
        if with_ai and settings.ai_enabled:
            decision = self._ask(out.read)
        else:
            decision = Decision(configured=False, error="الذكاء الاصطناعي معطّل لهذه الجولة")
        out.decision = decision

        # 4 + 5 — journal what came of it.
        if journal:
            price = out.price
            if decision.has_trade:
                out.journalled = log_signal(settings, decision, out.read.facts, price=price)
            elif decision.configured:
                out.journalled = log_no_trade(settings, decision, price=price)

        return out

    def _ask(self, read: Read) -> Decision:
        client = AiClient(self.settings)
        prompt = build_prompt(read.facts, read.news or None, self.settings.min_rr)
        try:
            reply, model = client.complete(SYSTEM_PROMPT, prompt)
        except AiBudgetError as exc:
            return Decision(configured=False, error=str(exc))
        except Exception as exc:
            return Decision(configured=False, error=f"تعذّر الوصول للنموذج: {exc}")

        raw = parse_json(reply)
        if raw is None:
            return Decision(configured=False, model=model, error="ردّ النموذج لم يكن JSON صالحاً")
        return decide(read.facts, raw, model=model, min_rr=self.settings.min_rr)

    def report(self) -> Report:
        return build_report(self.settings)

    def resolve_only(self) -> int:
        """Settle open signals without spending a model call."""
        return resolve_open(self.settings, self._feed_or_build())
