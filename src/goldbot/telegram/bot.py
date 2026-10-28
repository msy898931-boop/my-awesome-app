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
from typing import Optional

from ..config import Settings
from ..pipeline import Pipeline, Result
from .client import Telegram, TelegramError
from .format import format_analysis, format_news, format_plan

HELP = (
    "🥇 *بوت الذهب — SMC/ICT*\n\n"
    "اضغط زراً من الأزرار تحت، أو اكتب الأمر:\n"
    "/analyse — قراءة SMC/ICT الكاملة لكل فريم\n"
    "/plan — آخر صفقة أو حالة السوق الآن\n"
    "/news — التقويم الاقتصادي والعناوين\n"
    "/status — ملخّص سريع\n\n"
    "البوت يفحص السوق كل فترة ويرسل عند ظهور فكرة جديدة.\n"
    "تحليل استشاري فقط — لا ينفّذ أي صفقة."
)

# The buttons: label -> callback data. Callback data is ASCII and short,
# because Telegram caps it at 64 bytes, and the label carries the Arabic.
MENU: list[list[tuple[str, str]]] = [
    [("📊 التحليل الكامل", "analyse"), ("🎯 الصفقة", "plan")],
    [("📰 الأخبار", "news"), ("📈 حالة السوق", "status")],
]


def menu_keyboard() -> dict:
    """The inline keyboard attached to every card."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in MENU
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

    # ── state ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            with open(self.state_path, encoding="utf-8") as fh:
                state = json.load(fh)
            self._last_key = state.get("last_key")
            self._last_offset = state.get("last_offset")
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.state_path) or ".", exist_ok=True)
        try:
            with open(self.state_path, "w", encoding="utf-8") as fh:
                json.dump({"last_key": self._last_key, "last_offset": self._last_offset}, fh)
        except OSError:
            pass

    # ── identity ─────────────────────────────────────────────────────────

    @staticmethod
    def plan_key(result: Result) -> str:
        plan = result.plan or {}
        if plan.get("direction") not in {"buy", "sell"}:
            return "no-trade"
        return f"{plan['direction']}:{plan.get('entry')}:{plan.get('stop')}:{plan.get('target')}"

    # ── one cycle ────────────────────────────────────────────────────────

    def tick(self, with_ai: bool = True) -> Optional[Result]:
        """Run one analysis and push a card if the idea is new. Returns the result."""
        result = self.pipeline.run(with_ai=with_ai)
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
            text = format_plan(result.plan, result.analysis, result.news)
            self.tg.send(self.chat_id, text, keyboard=menu_keyboard())
            self._log(f"أُرسلت فكرة — {(result.plan or {}).get('direction', '?')}")
        return result

    # ── commands ─────────────────────────────────────────────────────────

    def handle_updates(self) -> None:
        updates = self.tg.updates(self._last_offset)
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
        """A button press: clear the spinner first, then answer in place.

        The spinner is cleared before any work, because Telegram keeps the
        button spinning until ``answerCallbackQuery`` lands and a slow read
        would otherwise look like a hung bot.
        """
        callback_id = str(callback.get("id", ""))
        data = str(callback.get("data", ""))
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
        self.tg.answer_callback(callback_id)
        if not chat_id:
            return
        self._answer(chat_id, data)

    def _answer(self, chat_id: str, data: str) -> None:
        """Render one card for a command or button, keyboard attached."""
        self._log(f"طلب: {data}")
        # The typing indicator goes out before the read, so the wait is visible.
        self.tg.typing(chat_id)
        try:
            if data == "analyse":
                result = self.pipeline.run(with_ai=False)
                body = format_analysis(result.facts, result.news)
            elif data == "plan":
                result = self.pipeline.run(with_ai=True)
                body = format_plan(result.plan, result.analysis, result.news)
            elif data == "news":
                result = self.pipeline.run(with_ai=False)
                body = format_news(result.news)
            elif data == "status":
                body = self._status_text(self.pipeline.run(with_ai=False))
            elif data in {"start", "help", "مساعدة"}:
                body = HELP
            else:
                body = "أمر غير معروف. اضغط زراً من الأزرار تحت."
            self.tg.send(chat_id, body, keyboard=menu_keyboard())
        except TelegramError:
            pass

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
        }
        self._answer(chat_id, aliases.get(command, command))

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
            ])
        except TelegramError:
            pass

    def run_forever(self) -> None:
        """Poll commands, then the market, then sleep. Runs until interrupted."""
        self._start_health()
        self._register_commands()
        self._log(f"البوت يعمل — كل {self.settings.poll_seconds} ثانية")
        cycle = 0
        while True:
            cycle += 1
            try:
                self.handle_updates()
            except TelegramError as exc:
                self._log(f"تحديثات: فشل ({exc})")
            try:
                self.tick()
                self._cycles = cycle
                if cycle % 20 == 0:
                    self._log(f"دورة {cycle}: لا جديد")
            except Exception as exc:
                # A bad cycle must not kill the loop; the next one may be fine.
                self._log(f"دورة {cycle}: خطأ ({type(exc).__name__}: {exc})")
            time.sleep(self.settings.poll_seconds)

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
        from ..health import start_health_server

        self._started_at = time.time()
        self._cycles = 0
        start_health_server(port, self.health_status)
        self._log(f"منفذ الصحة يعمل على {port}")

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
