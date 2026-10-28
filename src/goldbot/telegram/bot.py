"""The bot loop: poll, decide what is new, and say only that.

The loop's discipline is what makes it usable. On the first pass it seeds its
state from whatever is already on the board and sends nothing — a restart must
never replay the day's alerts. After that it sends a card only when the plan's
identity changes, where identity is direction plus the three levels. A plan
that merely wobbles by a tick is the same idea, and repeating it is noise.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import replace
from typing import Optional

from ..config import Settings
from ..pipeline import Pipeline, Result
from .client import Telegram, TelegramError
from .format import format_analysis, format_news, format_plan, format_price

HELP = (
    "🥇 *بوت الذهب — SMC/ICT*\n\n"
    "⌨️ *لوحة التحكم أسفل الشاشة* — الأزرار ثابتة هناك، اضغط أي زر في أي وقت:\n"
    "📊 التحليل · 🎯 الصفقة · 📰 الأخبار · 📈 الحالة · 🔄 تحديث · ⚙️ الإعدادات\n\n"
    "أو اكتب الأمر:\n"
    "/analyse — قراءة SMC/ICT الكاملة لكل فريم\n"
    "/plan — آخر صفقة أو حالة السوق الآن\n"
    "/news — التقويم الاقتصادي والعناوين\n"
    "/status — ملخّص سريع\n"
    "/refresh — قراءة جديدة فوراً (تتجاوز الكاش)\n"
    "/settings — تغيير الفريمات والحساسية\n\n"
    "البوت يفحص السوق كل فترة ويرسل عند ظهور فكرة جديدة.\n"
    "تحليل استشاري فقط — لا ينفّذ أي صفقة."
)

# Every frame the data providers can serve. Order is the display order, widest
# last, so the settings grid reads from fast to slow.
ALL_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

# Sensitivity is the minimum reward-to-risk the plan must clear. A lower bar
# admits more setups (more signals, thinner ones); a higher bar admits fewer,
# cleaner ones. Stored as (callback key, label, value).
SENSITIVITY: tuple[tuple[str, str, float], ...] = (
    ("1.5", "🟢 حساسية عالية", 1.5),
    ("2.0", "🟡 متوازنة", 2.0),
    ("3.0", "🔴 صارمة", 3.0),
)

# The buttons: label -> callback data. Callback data is ASCII and short,
# because Telegram caps it at 64 bytes, and the label carries the Arabic.
MENU: list[list[tuple[str, str]]] = [
    [("📊 التحليل الكامل", "analyse"), ("🎯 الصفقة", "plan")],
    [("📰 الأخبار", "news"), ("📈 حالة السوق", "status")],
    [("🔄 تحديث", "refresh"), ("⚙️ الإعدادات", "settings")],
]

# The persistent panel pinned to the bottom of the chat. A *reply* keyboard,
# not an inline one: it stays on screen while the trader scrolls, so a button
# is never lost inside an old card. Each button sends its own text, and the
# leading emoji is what ``_respond`` maps back to a command, so this list and
# MENU must keep the same emoji heads.
REPLY_MENU: list[list[str]] = [
    ["📊 التحليل", "🎯 الصفقة"],
    ["📰 الأخبار", "📈 الحالة"],
    ["🔄 تحديث", "⚙️ الإعدادات"],
]

PANEL_HINT = "⌨️ لوحة التحكم أسفل الشاشة — اضغط أي زر في أي وقت."

# Older releases used the leading emoji itself as the callback data. Telegram
# keeps those keyboards on every message already in the chat, so a tap on an
# old card still arrives here as "🔄" and used to dead-end on "أمر غير معروف".
# The map is derived from MENU rather than hand-written, so it can never drift.
_VARIATION_SELECTOR = "\ufe0f"


def _legacy_aliases() -> dict[str, str]:
    """Map a label, its leading emoji, and their VS16-stripped forms to a command."""
    aliases: dict[str, str] = {}
    for label, data in (b for row in MENU for b in row):
        head = label.split(" ", 1)[0]
        for key in (label, head):
            for form in (key, key.replace(_VARIATION_SELECTOR, "")):
                if form:
                    aliases[form] = data
    return aliases


LEGACY_ALIASES: dict[str, str] = _legacy_aliases()


def resolve_data(raw: str) -> str:
    """Normalise a button payload or typed command to a canonical command.

    Emoji payloads from pre-existing keyboards are translated here, and the
    emoji variation selector (U+FE0F) is dropped, because Telegram may or may
    not include it — ``⚙️`` and ``⚙`` must both resolve.
    """
    key = (raw or "").strip()
    if not key:
        return key
    return LEGACY_ALIASES.get(key) or LEGACY_ALIASES.get(
        key.replace(_VARIATION_SELECTOR, "")
    ) or key


# Settings the user may change from the bot, and where each is remembered on
# the instance. The value is written to the state file only once it is set,
# so an untouched preference keeps following the environment.
_OVERRIDE_ATTR = {"timeframes": "_tf_override", "min_rr": "_rr_override"}


def menu_keyboard() -> dict:
    """The inline keyboard attached to every card."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in MENU
        ]
    }


def reply_keyboard() -> dict:
    """The persistent panel pinned to the bottom of the chat.

    ``is_persistent`` keeps it on screen after a message is sent, and
    ``resize_keyboard`` stops it from swallowing half the screen. It is set
    once and stays for the chat, so the trader always has a button in reach
    instead of scrolling back to an old card.
    """
    return {
        "keyboard": [[{"text": label} for label in row] for row in REPLY_MENU],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def settings_keyboard(timeframes: tuple[str, ...], min_rr: float) -> dict:
    """The settings grid: frame toggles, sensitivity, and a way back.

    A frame is shown ticked when it is read. Sensitivity shows a tick on the
    option whose value matches the current minimum reward-to-risk.
    """
    active = set(timeframes)
    frame_rows: list[list[dict]] = [[], []]
    for index, tf in enumerate(ALL_TIMEFRAMES):
        frame_rows[index // 4].append({
            "text": f"{'✅' if tf in active else '⬜'} {tf}",
            "callback_data": f"tf:{tf}",
        })
    sens_row = [
        {"text": f"{'✅ ' if abs(min_rr - value) < 1e-9 else ''}{label}",
         "callback_data": f"sens:{key}"}
        for key, label, value in SENSITIVITY
    ]
    return {
        "inline_keyboard": [
            *[row for row in frame_rows if row],
            sens_row,
            [{"text": "🔄 تحديث", "callback_data": "refresh"},
             {"text": "↩️ رجوع", "callback_data": "menu"}],
        ]
    }


class Bot:
    """Polls the pipeline and pushes cards. Persists state across restarts."""

    def __init__(
        self, settings: Settings, pipeline: Optional[Pipeline] = None,
        state_path: str = "run/bot_state.json", tg: Optional[Telegram] = None,
    ) -> None:
        self.settings = settings
        self.pipeline = pipeline or Pipeline(settings)
        self.state_path = state_path
        self.tg = tg or Telegram(settings.telegram_token)
        self.chat_id = settings.telegram_chat
        self._last_key: Optional[str] = None
        self._last_offset: Optional[int] = None
        self._primed = False
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
            self._tf_override = state.get("timeframes")
            self._rr_override = state.get("min_rr")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        payload: dict = {"last_key": self._last_key, "last_offset": self._last_offset}
        if getattr(self, "_tf_override", None):
            payload["timeframes"] = list(self.settings.timeframes)
        if getattr(self, "_rr_override", None):
            payload["min_rr"] = self.settings.min_rr
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
        except OSError:
            pass

    # ── identity ─────────────────────────────────────────────────────────

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

    def tick(self, with_ai: bool = True) -> Optional[Result]:
        """Run one analysis and push a card if the idea is new. Returns the result.

        The automatic push is a *price* card and costs no model tokens: the AI
        is only asked when a card is actually due, and even then the push stays
        deterministic. Spending 3400 tokens per cycle on prose that is discarded
        on all but a handful of cycles is what drained the daily quota in an
        hour and a half.
        """
        result = self.pipeline.run(with_ai=False)
        key = self.plan_key(result)

        if not self._primed:
            # First pass: learn the current board, announce nothing.
            self._last_key = key
            self._primed = True
            self._save()
            return result

        if key != self._last_key:
            self._last_key = key
            self._save()
            text = format_price(result.spot, result.plan, result.news)
            try:
                ok = self.tg.send(self.chat_id, text, keyboard=menu_keyboard())
            except TelegramError as exc:
                self._log(f"فشل إرسال فكرة ({exc})")
                return result
            # The send result decides the log line: an unconditional "sent"
            # once made a rejected push look delivered.
            if ok:
                self._log(f"أُرسلت فكرة — {(result.plan or {}).get('direction', '?')}")
            else:
                self._log("فشل إرسال فكرة (رفض تيليجرام)")
        return result

    # ── commands ─────────────────────────────────────────────────────────

    def handle_updates(self, timeout: int = 0) -> None:
        """Drain pending updates. ``timeout`` > 0 makes the call long-poll.

        Long polling is what makes a button feel instant: ``getUpdates`` blocks
        until something arrives instead of the loop only looking once per
        ``poll_seconds``. Without it a press could sit unseen for a full minute
        and the bot looked dead.
        """
        updates = self.tg.updates(self._last_offset, timeout=timeout)
        for update in updates:
            self._last_offset = update.get("update_id", 0) + 1
            callback = update.get("callback_query")
            if callback:
                self._handle_callback(callback)
                continue
            message = update.get("message") or update.get("edited_message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            self._respond(chat_id, text)
        self._save()

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

    def _answer(self, chat_id: str, data: str, callback_id: str = "") -> None:
        """Render one screen for a command or a button press."""
        self._log(f"طلب: {data}")

        # Settings and navigation are instant — no market read — so the toast
        # and the re-rendered screen can go out in one pass.
        if data == "menu":
            if callback_id:
                self.tg.answer_callback(callback_id)
            self.tg.send(chat_id, HELP, keyboard=menu_keyboard())
            self.tg.send(chat_id, PANEL_HINT, markdown=False, keyboard=reply_keyboard())
            return
        if data == "settings" or data.startswith("tf:") or data.startswith("sens:"):
            body, keyboard, toast = self._settings_screen(data)
            if callback_id:
                self.tg.answer_callback(callback_id, toast)
            self.tg.send(chat_id, body, keyboard=keyboard)
            return

        # Everything else reads the market: clear the spinner, then show typing.
        if callback_id:
            self.tg.answer_callback(callback_id)
        self.tg.typing(chat_id)
        # A refresh is a deliberate "read again now", so it bypasses the cache.
        fresh = 0.0 if data == "refresh" else None
        try:
            if data in {"analyse", "refresh"}:
                result = self.pipeline.run(with_ai=False, max_age=fresh)
                body = format_analysis(result.facts, result.news)
            elif data == "plan":
                result = self.pipeline.run(with_ai=True, max_age=fresh)
                body = format_plan(result.plan, result.analysis, result.news)
            elif data == "news":
                result = self.pipeline.run(with_ai=False, max_age=fresh)
                body = format_news(result.news)
            elif data == "status":
                body = self._status_text(self.pipeline.run(with_ai=False, max_age=fresh))
            elif data in {"start", "help", "مساعدة"}:
                body = HELP
            else:
                body = "أمر غير معروف. اضغط زراً من الأزرار تحت."
            self.tg.send(chat_id, body, keyboard=menu_keyboard())
            # Re-pin the panel whenever the help text is shown, so a fresh
            # /start always leaves a button within reach.
            if data in {"start", "help", "مساعدة"}:
                self.tg.send(chat_id, PANEL_HINT, markdown=False,
                             keyboard=reply_keyboard())
        except TelegramError:
            pass

    def _settings_screen(self, data: str) -> tuple[str, dict, str]:
        """Apply one settings action and return the screen, keyboard and toast."""
        toast = ""
        if data.startswith("tf:"):
            frame = data[3:].upper()
            current = list(self.settings.timeframes)
            if frame in current:
                if len(current) == 1:
                    toast = "لازم يبقى فريم واحد على الأقل"
                else:
                    current.remove(frame)
                    toast = f"أُوقف {frame}"
            elif frame in ALL_TIMEFRAMES:
                current.append(frame)
                toast = f"أُضيف {frame}"
            if toast and toast.startswith(("أُوقف", "أُضيف")):
                ordered = tuple(tf for tf in ALL_TIMEFRAMES if tf in set(current))
                self._reconfigure(timeframes=ordered)
        elif data.startswith("sens:"):
            key = data[5:]
            match = next((v for k, _, v in SENSITIVITY if k == key), None)
            if match is not None:
                self._reconfigure(min_rr=match)
                toast = f"الحساسية الآن 1:{match:g}"

        frames = self.settings.timeframes
        body = (
            "⚙️ *الإعدادات*\n\n"
            f"*الفريمات المقروءة:* {', '.join(frames)}\n"
            f"*الحساسية (أدنى مخاطرة/مكافأة):* `1:{self.settings.min_rr:g}`\n\n"
            "اضغط فريماً لتفعيله أو إيقافه.\n"
            "الحساسية: حساسية عالية = صفقات أكثر وأقل نظافة، "
            "صارمة = صفقات أقل وأنقى.\n\n"
            "_التغييرات تُحفظ وتستمر بعد إعادة التشغيل._"
        )
        return body, settings_keyboard(frames, self.settings.min_rr), toast

    @staticmethod
    def _status_text(result: Result) -> str:
        lines = [
            f"🥇 *XAUUSD* — {result.symbol}",
            f"الفريمات المقروءة: {', '.join(result.sources.keys()) or '—'}",
            f"المصادر: {', '.join(set(result.sources.values())) or '—'}",
        ]
        if result.spot:
            lines.append(f"تحقق السعر: فرق {result.spot.get('max_divergence_pct')}% "
                         f"({'موثوق' if result.spot.get('trusted') else 'مشكوك'})")
        if result.errors:
            lines.append(f"أخطاء: {len(result.errors)}")
        return "\n".join(lines)

    def _respond(self, chat_id: str, text: str) -> None:
        command = text.split()[0].lower().lstrip("/")
        aliases = {
            "تحليل": "analyse", "analysis": "analyse", "ta": "analyse",
            "صفقة": "plan", "trade": "plan", "signal": "plan",
            "أخبار": "news", "اخبار": "news", "news": "news",
            "حالة": "status", "status": "status",
            "تحديث": "refresh", "refresh": "refresh", "reload": "refresh",
            "إعدادات": "settings", "اعدادات": "settings", "settings": "settings",
            "menu": "menu", "قائمة": "menu",
        }
        self._answer(chat_id, resolve_data(aliases.get(command, command)))

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
        try:
            self.tg.set_commands([
                ("start", "القائمة والمساعدة"),
                ("analyse", "قراءة SMC/ICT الكاملة"),
                ("plan", "آخر صفقة أو حالة السوق"),
                ("news", "التقويم والعناوين"),
                ("status", "ملخّص سريع"),
                ("refresh", "قراءة جديدة فوراً"),
                ("settings", "الفريمات والحساسية"),
            ])
        except TelegramError:
            pass

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
            ok = self.tg.send(self.chat_id, PANEL_HINT, markdown=False,
                              keyboard=reply_keyboard())
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
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {message}", flush=True)
