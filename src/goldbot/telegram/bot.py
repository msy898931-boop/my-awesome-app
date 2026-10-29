"""The bot loop: poll, decide what is new, and say only that.

The loop's discipline is what makes it usable. On the first pass it seeds its
state from whatever is already on the board and sends nothing — a restart must
never replay the day's alerts. After that it sends a card only when the plan's
identity changes, where identity is the direction plus the setup. A plan that
merely wobbles by a tick is the same idea, and repeating it is noise. During a
high-impact blackout the idea is withheld and remembered, then delivered once
the window closes.

This module owns the *loop and the state*: the poll cadence, the pushed card,
the persisted preferences, and the health port. Screen rendering lives in
:mod:`~goldbot.telegram.screens`; the buttons and help text in
:mod:`~goldbot.telegram.menus`. The names those modules export are re-exported
here, so a caller that always imported them from ``bot`` keeps working.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import replace

from ..config import Settings
from ..pipeline import Pipeline, Result
from .alerts import alert_key, due_news_alerts, prune
from .client import Telegram, TelegramError
from .format import blackout_blocks_idea, format_alert, format_price
from .menus import (
    ALL_TIMEFRAMES,
    HELP,
    MENU,
    PANEL_HINT,
    REPLY_MENU,
    SENSITIVITY,
    menu_keyboard,
    reply_keyboard,
    resolve_data,
    settings_keyboard,
)
from .screens import CommandScreens

# The names this module historically exported. Listing them here marks the
# re-exported imports above as intentional rather than unused.
__all__ = [
    "ALL_TIMEFRAMES",
    "Bot",
    "HELP",
    "MENU",
    "PANEL_HINT",
    "REPLY_MENU",
    "SENSITIVITY",
    "menu_keyboard",
    "reply_keyboard",
    "resolve_data",
    "settings_keyboard",
]

log = logging.getLogger("goldbot")

# Settings the user may change from the bot, and where each is remembered on
# the instance. The value is written to the state file only once it is set,
# so an untouched preference keeps following the environment.
_OVERRIDE_ATTR = {"timeframes": "_tf_override", "min_rr": "_rr_override"}


class Bot(CommandScreens):
    """Polls the pipeline and pushes cards. Persists state across restarts."""

    def __init__(
        self,
        settings: Settings,
        pipeline: Pipeline | None = None,
        state_path: str = "run/bot_state.json",
        tg: Telegram | None = None,
    ) -> None:
        self.settings = settings
        self.pipeline = pipeline or Pipeline(settings)
        self.state_path = state_path
        self.tg = tg or Telegram(settings.telegram_token)
        self.chat_id = settings.telegram_chat
        self._last_key: str | None = None
        self._last_offset: int | None = None
        self._pending_key: str | None = None
        self._primed = False
        # Event key -> epoch announced. Persisted, so a restart cannot re-fire a
        # heads-up the trader has already seen.
        self._announced: dict[str, float] = {}
        # UTC day and the AI calls made within it, so the daily cap survives a
        # restart instead of resetting on every deploy.
        self._ai_day: str = ""
        self._ai_calls: int = 0
        self._load()
        # Preferences live in the state file so a restart keeps them, and a
        # rebuilt pipeline is what makes a change take effect immediately.
        self._apply_overrides()

    # ── preferences ──────────────────────────────────────────────────────

    def _apply_overrides(self) -> None:
        """Rebuild settings and pipeline from the saved preferences.

        Only a preference the user actually changed is written to the state
        file, so an environment change still takes effect for anything the user
        never touched. The feed is carried over, so a custom one survives.
        """
        overrides: dict = {}
        frames = getattr(self, "_tf_override", None)
        if frames:
            overrides["timeframes"] = tuple(frames)
        rr = getattr(self, "_rr_override", None)
        if rr:
            overrides["min_rr"] = float(rr)
        if overrides:
            self.settings = replace(self.settings, **overrides)
            self.pipeline = Pipeline(self.settings, feed=self.pipeline.feed)

    def _reconfigure(self, **overrides) -> None:
        """Apply a settings change now and remember it for the next start."""
        for key, value in overrides.items():
            setattr(self, _OVERRIDE_ATTR[key], value)
        self.settings = replace(self.settings, **overrides)
        self.pipeline = Pipeline(self.settings, feed=self.pipeline.feed)
        self._save()

    # ── state ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            self._last_key = state.get("last_key")
            self._last_offset = state.get("last_offset")
            self._pending_key = state.get("pending_key")
            self._tf_override = state.get("timeframes")
            self._rr_override = state.get("min_rr")
            self._announced = dict(state.get("announced") or {})
            self._ai_day = str(state.get("ai_day") or "")
            self._ai_calls = int(state.get("ai_calls") or 0)
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        payload: dict = {
            "last_key": self._last_key,
            "last_offset": self._last_offset,
            "pending_key": self._pending_key,
            "announced": self._announced,
            "ai_day": self._ai_day,
            "ai_calls": self._ai_calls,
        }
        if getattr(self, "_tf_override", None):
            payload["timeframes"] = list(self.settings.timeframes)
        if getattr(self, "_rr_override", None):
            payload["min_rr"] = self.settings.min_rr
        # Written to a temp file and moved into place: a crash mid-write must
        # not leave a half-state that the next start reads as truth. The whole
        # thing is guarded, ``makedirs`` included — a bad state path (a
        # read-only mount, a directory removed under us) must not raise out of
        # here, because ``handle_updates`` calls this and the loop only catches
        # ``TelegramError``: an escaping ``OSError`` would stop the bot dead.
        tmp = f"{self.state_path}.tmp"
        try:
            os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self.state_path)
        except OSError:
            with contextlib.suppress(OSError):
                if os.path.exists(tmp):
                    os.unlink(tmp)

    # ── identity ─────────────────────────────────────────────────────────

    def _ai_today(self) -> int:
        """AI calls made so far today (UTC). Rolls the counter at the date change."""
        today = time.strftime("%Y-%m-%d", time.gmtime())
        if today != self._ai_day:
            self._ai_day = today
            self._ai_calls = 0
        return self._ai_calls

    def _count_ai_call(self) -> None:
        self._ai_calls = self._ai_today() + 1
        self._save()

    @staticmethod
    def plan_key(result: Result) -> str:
        """The identity of an *idea*, not of its numbers.

        Direction plus setup. The entry/stop/target deliberately do not appear:
        they drift by a tick on every read, so including them made a single
        idea look new on almost every cycle — the bot sent the same "buy" eight
        times in forty minutes. A trader wants to hear about a new idea, not
        about the same idea re-priced.
        """
        plan = result.plan or {}
        if plan.get("direction") not in {"buy", "sell"}:
            return "no-trade"
        return f"{plan['direction']}:{plan.get('setup') or ''}"

    # ── one cycle ────────────────────────────────────────────────────────

    def tick(self, with_ai: bool = True) -> Result | None:
        """Run one analysis and push a card if the idea is new. Returns the result.

        Two unprompted things happen here, and nothing else:

          * a **news heads-up** when a high-impact release is inside the lead
            window — the one fact a trader cannot check for themselves;
          * a **trade card** when the idea is new, carrying the numbers and, if
            the budget allows, the model's read.

        The AI is asked only when a card is actually due, and only within a hard
        daily cap: spending tokens on prose that is discarded on all but a
        handful of cycles is what drained the free tier in an hour and a half.
        """
        result = self.pipeline.run(with_ai=False)
        key = self.plan_key(result)

        if not self._primed:
            # First pass: learn the current board, announce nothing — but mark
            # the events already inside the lead window as announced, so a
            # restart does not fire a burst of alerts for news the trader has
            # already been living through.
            self._last_key = key
            self._primed = True
            for event in due_news_alerts(
                result.news, self.settings.news_alert_minutes, self._announced
            ):
                self._announced[alert_key(event)] = time.time()
            self._save()
            return result

        self._announce_news(result.news)

        # A blackout withholds *new ideas*, not the price. During a high-impact
        # release the card still goes out as a price read with the window
        # flagged, but without the direction and setup: the rule is stated, so
        # it is honoured rather than merely printed.
        in_blackout = blackout_blocks_idea(result.news)
        withheld = in_blackout and key != "no-trade"

        if key != self._last_key or (self._pending_key and not in_blackout):
            self._last_key = key
            if withheld:
                # Remember the idea so it is delivered once the window closes
                # instead of being silently swallowed by the price card.
                self._pending_key = key
            else:
                self._pending_key = None
            self._save()
            analysis = self._enrich(result) if (with_ai and not withheld) else None
            text = format_price(
                result.spot,
                None if withheld else result.plan,
                result.news,
                withheld=withheld,
                analysis=analysis,
            )
            try:
                ok = self.tg.send(self.chat_id, text, keyboard=menu_keyboard())
            except TelegramError as exc:
                self._log(f"فشل إرسال فكرة ({exc})")
                return result
            # The send result decides the log line: an unconditional "sent"
            # once made a rejected push look delivered.
            if not ok:
                self._log("فشل إرسال فكرة (رفض تيليجرام)")
            elif withheld:
                self._log("نافذة خبر — أُرسل السعر بلا فكرة جديدة")
            else:
                self._log(f"أُرسلت فكرة — {(result.plan or {}).get('direction', '?')}")
        return result

    def _announce_news(self, news: dict | None) -> None:
        """Send a heads-up for each high-impact release inside the lead window.

        Keyed and persisted, so an event is announced exactly once no matter how
        often the loop runs or how often it restarts.
        """
        for event in due_news_alerts(news, self.settings.news_alert_minutes, self._announced):
            text = format_alert(event, news)
            try:
                ok = self.tg.send(self.chat_id, text, keyboard=menu_keyboard())
            except TelegramError as exc:
                self._log(f"فشل تنبيه خبر ({exc})")
                continue
            # Marked announced only once it is actually sent: a failed send must
            # be retried on the next cycle, not swallowed as "done".
            if ok:
                self._announced[alert_key(event)] = time.time()
                self._log(f"تنبيه خبر — {event.get('title')} بعد {event.get('in_minutes')} د")
            else:
                self._log("فشل تنبيه خبر (رفض تيليجرام)")
        self._announced = prune(self._announced)

    def _enrich(self, result: Result) -> dict | None:
        """Ask the model for prose, within a hard daily cap. ``None`` when capped.

        The cap exists because the free tier is metered per day, and a loop that
        calls the model on every new idea can exhaust it and then look broken
        until midnight. When the cap is reached the deterministic plan still
        goes out — the card loses the commentary, never the trade.
        """
        if not self.settings.ai_enabled:
            return None
        if self._ai_today() >= self.settings.ai_daily_cap:
            return None
        enriched = self.pipeline.analyse(result)
        self._count_ai_call()
        analysis = enriched.analysis or None
        # A fallback carries no reasoning; do not spend the cap's good name on it.
        if analysis and not (analysis.get("reasoning") or analysis.get("confidence")):
            return None
        return analysis

    # ── commands ─────────────────────────────────────────────────────────

    def handle_updates(self, timeout: int = 0) -> None:
        """Drain pending updates. ``timeout`` > 0 makes the call long-poll.

        Long polling is what makes a button feel instant: ``getUpdates`` blocks
        until something arrives instead of the loop only looking once per
        ``poll_seconds``. Without it a press could sit unseen for a full minute
        and the bot looked dead.

        Only the configured chat is served. An answer is a market read, and a
        read can cost an AI call, so an open door lets anyone who finds the bot
        spend the day's Groq quota; a stranger is logged and dropped instead.
        """
        updates = self.tg.updates(self._last_offset, timeout=timeout)
        for update in updates:
            self._last_offset = update.get("update_id", 0) + 1
            callback = update.get("callback_query")
            if callback:
                chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
                if not self._authorized(chat_id):
                    self._log(f"تجاهل ضغطة زر من محادثة غير مصرّحة ({chat_id or '؟'})")
                    continue
                self._handle_callback(callback)
                continue
            message = update.get("message") or update.get("edited_message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            if not self._authorized(chat_id):
                self._log(f"تجاهل رسالة من محادثة غير مصرّحة ({chat_id})")
                continue
            self._respond(chat_id, text)
        self._save()

    def _authorized(self, chat_id: str) -> bool:
        """Whether this chat is the configured one.

        With no ``TELEGRAM_CHAT_ID`` there is no configured owner, so every chat
        is refused rather than every chat admitted — the failure mode of a
        missing setting must not be an open bot.
        """
        allowed = str(self.chat_id or "").strip()
        return bool(allowed) and str(chat_id).strip() == allowed

    def _handle_callback(self, callback: dict) -> None:
        """A button press: identify it, then answer in place.

        ``answerCallbackQuery`` is called before any work, because Telegram
        keeps the button spinning until it lands and a slow read would
        otherwise look like a hung bot.
        """
        callback_id = str(callback.get("id", ""))
        data = resolve_data(str(callback.get("data", "")))
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
        if not chat_id:
            self.tg.answer_callback(callback_id)
            return
        self._answer(chat_id, data, callback_id=callback_id)

    # ── loop ─────────────────────────────────────────────────────────────

    def _register_commands(self) -> None:
        """Publish the command list so Telegram shows a menu on ``/``.

        Names must be ASCII — Telegram rejects Arabic ones with
        ``BOT_COMMAND_INVALID`` — so the menu uses Latin names and carries the
        Arabic in the description. The Arabic spellings still work as typed
        text, since ``_respond`` maps them.
        Failure is silent: an unregistered menu is a cosmetic loss, and it must
        never stop the loop from starting.
        """
        with contextlib.suppress(TelegramError):
            self.tg.set_commands(
                [
                    ("start", "القائمة والمساعدة"),
                    ("analyse", "قراءة SMC/ICT الكاملة"),
                    ("plan", "آخر صفقة أو حالة السوق"),
                    ("news", "التقويم والعناوين"),
                    ("status", "ملخّص سريع"),
                    ("refresh", "قراءة جديدة فوراً"),
                    ("settings", "الفريمات والحساسية"),
                ]
            )

    def install_panel(self) -> bool:
        """Put the persistent panel at the bottom of the configured chat.

        Sent once at start so the buttons exist before the first card. A failure
        is logged, not fatal: the panel is a convenience, and the loop must
        still run without it.
        """
        if not str(self.chat_id or "").strip():
            self._log("لا يوجد TELEGRAM_CHAT_ID — بلا لوحة تحكم")
            return False
        try:
            ok = self.tg.send(self.chat_id, PANEL_HINT, markdown=False, keyboard=reply_keyboard())
        except TelegramError as exc:
            self._log(f"تعذّر إنشاء لوحة التحكم ({exc})")
            return False
        if ok:
            self._log("لوحة التحكم جاهزة أسفل الشاشة")
        return ok

    def run_forever(self) -> None:
        """Poll commands, then the market, then sleep. Runs until interrupted.

        The two cadences are separated. ``getUpdates`` long-polls, so a button
        press is answered within a second; the market cycle keeps its own
        ``poll_seconds`` interval and is not re-run for every update. The old
        single-sleep loop only looked for input once a minute, which is why a
        press could sit unanswered long enough to look broken.
        """
        self._start_health()
        self._register_commands()
        self.install_panel()
        self._log(f"البوت يعمل — كل {self.settings.poll_seconds} ثانية")
        cycle = 0
        next_cycle = 0.0
        # Below Telegram's own ~50s ceiling on getUpdates, so the call returns
        # regularly and the loop still notices a stop.
        long_poll = max(1, min(30, int(self.settings.poll_seconds)))
        while True:
            try:
                self.handle_updates(timeout=long_poll)
            except TelegramError as exc:
                self._log(f"تحديثات: فشل ({exc})")
                time.sleep(2)

            if time.monotonic() < next_cycle:
                continue

            cycle += 1
            try:
                self.tick()
                self._cycles = cycle
                if cycle % 20 == 0:
                    self._log(f"دورة {cycle}: لا جديد")
            except Exception as exc:
                # A bad cycle must not kill the loop; the next one may be fine.
                self._log(f"دورة {cycle}: خطأ ({type(exc).__name__}: {exc})")
            next_cycle = time.monotonic() + max(1.0, self.settings.poll_seconds)

    def _start_health(self) -> None:
        """Open the health port when the host provides one (Render, Fly, Heroku).

        Locally there is no ``PORT``, so nothing is opened and the loop behaves
        exactly as it does without this module.
        """
        raw = os.environ.get("PORT", "").strip()
        if not raw:
            return
        try:
            port = int(raw)
        except ValueError:
            self._log(f"PORT غير صالح ({raw!r}) — بدون منفذ صحة")
            return
        from ..health import SELF_PING_SECONDS, start_health_server, start_self_ping

        self._started_at = time.time()
        self._cycles = 0
        start_health_server(port, self.health_status)
        self._log(f"منفذ الصحة يعمل على {port}")

        # Render hands the service its own public URL. Pinging it is what keeps
        # a Free instance from being spun down after 15 idle minutes; relying on
        # an external cron proved unreliable, so the service guards itself.
        external = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        if external:
            start_self_ping(external)
            self._log(f"منبّه ذاتي كل {int(SELF_PING_SECONDS)} ثانية ({external})")
        else:
            # Say so out loud: a missing URL means the service will idle out
            # after 15 minutes, and a silent skip would look like a healthy run.
            self._log("لا يوجد RENDER_EXTERNAL_URL — بلا منبّه ذاتي")

    def health_status(self) -> dict:
        return {
            "status": "ok",
            "bot": "@Xauusd1435_bot",
            "uptime_seconds": round(time.time() - getattr(self, "_started_at", time.time())),
            "cycles": getattr(self, "_cycles", 0),
            "poll_seconds": self.settings.poll_seconds,
            "ai_enabled": self.settings.ai_enabled,
        }

    def _log(self, message: str) -> None:
        log.info("%s", message)
