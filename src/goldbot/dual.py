"""Run the bot and AYZR's own bot together, on one host and one port.

AYZR is a separate engine with a separate Telegram identity
(``@AYZR1435_bot``), and the month-long shadow test wants it posting in a chat
of its own, not into the bot it shadows. Keeping that separate requires a second
process — unless the two share a host, which is what this module is for.

The problem it solves is a hosting one, not a design one. Render's free tier
gives one web service and 750 instance-hours a month: two always-on services
would need 1440 hours and would suspend *every* free service in the workspace.
But a free instance must still answer one ``/health`` on ``$PORT``, and a second
process cannot bind that port. So instead of a second service, both bots run
here, in one process, each in its own thread:

  * the bot keeps its own loop, its own token, its own chat;
  * AYZR keeps its own loop, its own token, its own chat, its own journal;
  * one health server on ``$PORT`` reports both, keyed ``bot`` and ``ayzr``;
  * one shadow runner runs — AYZR's, started by the bot's ``_start_ayzr`` — so
    the journal is not written twice.

Nothing here changes either engine. Both are reached through their existing
public methods, and each keeps the isolation its own module already documents.
"""

from __future__ import annotations

import logging
import os
import threading
import time

from .ayzr.bot import AyzrBot
from .ayzr.config import AyzrSettings
from .config import Settings
from .telegram.bot import Bot

log = logging.getLogger("goldbot.dual")


def _thread(fn, name: str) -> threading.Thread:
    thread = threading.Thread(target=fn, daemon=True, name=name)
    thread.start()
    return thread


class DualHealth:
    """One health document for two bots, so a supervisor sees both.

    A single endpoint is a compromise of the free tier, not a preference: the
    platform routes one port. The compromise is contained by naming each
    engine's block, so "the bot is up" and "AYZR is up" stay distinguishable in
    the one place they are not separated.
    """

    def __init__(self, bot: Bot, ayzr: AyzrBot | None) -> None:
        self._bot = bot
        self._ayzr = ayzr
        self._started_at = time.time()

    def status(self) -> dict:
        return {
            "status": "ok",
            "uptime_seconds": round(time.time() - self._started_at),
            "bot": self._bot.health_status(),
            "ayzr": self._ayzr.health_status() if self._ayzr is not None else None,
        }


def _open_health(status) -> None:  # noqa: ANN001 (a callable returning dict)
    """Serve one health document and keep the free instance awake.

    The bot's own ``_start_health`` is skipped under the supervisor, because two
    binds on ``$PORT`` is an ``EADDRINUSE``. This is that same job, done once,
    for the document that covers both engines.
    """
    raw = os.environ.get("PORT", "").strip()
    if not raw:
        log.info("لا يوجد PORT — بلا منفذ صحة")
        return
    try:
        port = int(raw)
    except ValueError:
        log.warning("PORT غير صالح (%r) — بلا منفذ صحة", raw)
        return

    from .health import SELF_PING_SECONDS, start_health_server, start_self_ping

    start_health_server(port, status)
    log.info("منفذ الصحة يعمل على %d (البوت + AYZR)", port)

    external = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
    if external:
        start_self_ping(external)
        log.info("منبّه ذاتي كل %d ثانية (%s)", int(SELF_PING_SECONDS), external)
    else:
        log.warning("لا يوجد RENDER_EXTERNAL_URL — بلا منبّه ذاتي")


def run(settings: Settings | None = None, ayzr_settings: AyzrSettings | None = None) -> int:
    """Start both bots and block until the process is stopped.

    The bot runs on the calling thread (it is the primary loop); AYZR runs
    beside it. AYZR is started only when it has its own token — without one its
    loop would run headless and write nothing a chat could read, so the service
    says so and the bot stands alone.
    """
    settings = settings or Settings()
    ayzr_settings = ayzr_settings or AyzrSettings()

    bot = Bot(settings)

    # AYZR's loop, only if it has an identity. It does *not* start its own
    # shadow runner: the bot's ``_start_ayzr`` already runs one, and two
    # runners would write every signal to the journal twice.
    ayzr_bot: AyzrBot | None = None
    if ayzr_settings.telegram_token.strip():
        ayzr_bot = AyzrBot(ayzr_settings)
        _thread(lambda: ayzr_bot.run_forever(open_health=False, start_runner=False), "ayzr-bot")
        log.info("AYZR بوت منفصل يعمل في نفس الخدمة (شات AYZR)")
    else:
        log.warning("لا AYZR_TELEGRAM_BOT_TOKEN — البوت الأساسي وحده، بلا واجهة AYZR")

    _open_health(DualHealth(bot, ayzr_bot).status)
    bot.run_forever(open_health=False, start_ayzr=True)
    return 0


def main() -> int:
    """Console-script entrypoint: ``python -m goldbot.dual``."""
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    # Both bots need a token to be worth starting. Without the main bot's
    # there is no primary loop to run, and the process would exit at once.
    if not Settings().telegram_token.strip():
        log.error("لا TELEGRAM_BOT_TOKEN — لا شيء ليعمل")
        return 1

    port = os.environ.get("PORT", "").strip()
    if not port:
        log.warning("لا يوجد PORT — بلا منفذ صحة (يعمل محلياً بلا واجهة)")

    return run()


if __name__ == "__main__":  # pragma: no cover - exercised via the console script
    raise SystemExit(main())
