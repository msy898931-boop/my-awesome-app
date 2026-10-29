"""AYZR's shadow runner — the clock the one-month test needs.

The engine is a single pass: read, ask, validate, journal. A *test* needs that
pass to happen on its own, once per bar, for a month, without anyone pressing a
button. That is all this module is.

Two rules shape it, and both come from faults the main bot already paid for:

  * **The clock is the bar, not the minute.** AYZR signals on ``primary_tf``, so
    it runs once per *closed bar* of that frame and no more often. Polling every
    minute would ask the same question sixty times an hour; the journal guard
    would drop the repeats, but the model call would already have been spent.
  * **A bad pass must not kill the clock.** Every fault is caught and logged, and
    the loop simply waits for the next bar. A runner that dies on one network
    error is a runner that silently stops collecting halfway through the month.

It runs in a daemon thread when the main bot is up (see ``Bot._start_ayzr``), or
on its own via ``python -m goldbot.ayzr.cli loop``. It writes only under AYZR's
own data root, so it cannot touch the bot's state or archive.
"""

from __future__ import annotations

import logging
import threading
import time

from .config import AyzrSettings
from .engine import Engine

log = logging.getLogger("goldbot.ayzr")

# Seconds in one bar of each frame AYZR might signal on. Kept local rather than
# shared: AYZR's cadence is AYZR's business, and a change here cannot move the
# main bot's poll.
_TF_SECONDS = {
    "M1": 60,
    "M5": 300,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H4": 14400,
    "D1": 86400,
}


def interval_for(tf: str, override: int = 0) -> int:
    """How many seconds to wait between passes, from the frame AYZR signals on.

    An unknown frame falls back to M15's 900s rather than a guess, and the
    result is never below a minute so a typo cannot spin the loop.
    """
    if override > 0:
        return max(60, override)
    return max(60, _TF_SECONDS.get(tf.upper(), 900))


class ShadowRunner:
    """Runs AYZR on the bar clock. One instance per process is enough."""

    def __init__(self, settings: AyzrSettings | None = None, feed=None) -> None:
        self.settings = settings or AyzrSettings()
        self.engine = Engine(self.settings, feed=feed)
        self.interval = interval_for(
            self.settings.primary_tf, getattr(self.settings, "interval_seconds", 0)
        )
        self.passes = 0

    def run_once(self) -> str:
        """One pass. Returns a short line describing what happened, never raises."""
        try:
            run = self.engine.run(with_ai=True, journal=True)
        except Exception as exc:  # a pass must not take the clock down with it
            log.warning("AYZR دورة فاشلة: %s: %s", type(exc).__name__, exc)
            return f"خطأ: {type(exc).__name__}"

        self.passes += 1
        if not run.read.ok:
            return "لا قراءة"
        if run.skipped:
            return f"فكرة مفتوحة (تخطٍّ): {run.skipped}"
        if run.journalled:
            kind = run.journalled.get("kind")
            if kind == "signal":
                return f"إشارة: {run.decision.trade.get('direction')}"
            return "لا صفقة"
        return "لا جديد"

    def run_forever(self, stop: threading.Event | None = None) -> None:
        """Run a pass per bar until ``stop`` is set. Never raises.

        The first pass runs immediately, so a fresh start collects a reading
        rather than waiting a full bar for the first one.
        """
        log.info("AYZR يعمل — دورة كل %d ثانية (فريم %s)", self.interval, self.settings.primary_tf)
        while stop is None or not stop.is_set():
            line = self.run_once()
            log.info("AYZR: %s", line)
            if stop is not None:
                if stop.wait(self.interval):
                    return
            else:
                time.sleep(self.interval)

    def start_thread(self) -> threading.Thread:
        """Start the loop in a daemon thread. Returns the thread."""
        thread = threading.Thread(target=self.run_forever, daemon=True, name="ayzr-shadow")
        thread.start()
        return thread


__all__ = ["ShadowRunner", "interval_for"]
