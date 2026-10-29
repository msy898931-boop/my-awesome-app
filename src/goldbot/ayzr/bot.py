"""AYZR's own bot: a standalone Telegram service for the shadow engine.

The main bot's panel reaches AYZR through one button, which is fine for a
look. The one-month test needs something else: AYZR posting its readings
somewhere of its own, on a clock of its own, in a chat of its own — so the
journal can be read without the bot that shadows it in the way. That is what
this module is.

It is a *separate service*, not a mode of the main bot:

  * its own token (``AYZR_TELEGRAM_BOT_TOKEN``) and its own chat
    (``AYZR_TELEGRAM_CHAT_ID``) — never the bot's, so a shadow test cannot
    post into the chat it is meant to be compared against;
  * its own command offset in its own state file, so the two loops never
    consume each other's updates;
  * its own health port on ``PORT``, so a host can keep it alive the same way
    it keeps the bot alive;
  * the same :class:`~.runner.ShadowRunner` the CLI runs, started in a daemon
    thread when ``AYZR_ENABLED`` is set.

Nothing here is load-bearing for the main bot. A fault in AYZR is logged and
the bot never sees it, which is the whole reason AYZR is a second engine.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from pathlib import Path

from ..telegram.client import Telegram, TelegramError
from ..telegram.menus import menu_keyboard
from .cards import format_decision, format_report
from .config import AyzrSettings
from .engine import Engine
from .runner import ShadowRunner

log = logging.getLogger("goldbot.ayzr")

HELP = (
    "🤖 *AYZR — محرّك الذهب بالذكاء الاصطناعي*\n\n"
    "هذا محرّك منفصل عن بوت الذهب الأساسي: النموذج هو من *يقرّر* الصفقة، "
    "ثم يتحقق الحساب منها قبل تسجيلها. حصّته الخاصة، ودفتره الخاص.\n\n"
    "⌨️ *الأزرار أسفل الشاشة:*\n"
    "🤖 تحليل — قراءة AYZR الكاملة وصفقتها المقترحة\n"
    "📒 الدفتر — نتائج الإشارات: اليوم والإجمالي والحكم\n"
    "🔄 تحديث — حسم الإشارات المفتوحة مقابل الشريط\n\n"
    "أو اكتب: /ayzr · /دفتري · /resolve\n\n"
    "_الاختبار الشهري: كل إشارة تُقارن بسيطرة عشوائية، ولا حكم قبل 100 إشارة "
    "محسومة. تحليل استشاري فقط — لا ينفّذ أي صفقة._"
)

# Typed command (Arabic or Latin) -> canonical action. The panel's buttons send
# their label text, so the emoji heads are mapped too, exactly as the main bot
# maps its own panel.
_COMMANDS: dict[str, str] = {
    "start": "help",
    "help": "help",
    "مساعدة": "help",
    "قائمة": "help",
    "ayzr": "analyse",
    "analyze": "analyse",
    "analyse": "analyse",
    "تحليل": "analyse",
    "🤖": "analyse",
    "دفتري": "report",
    "دفتر": "report",
    "report": "report",
    "📒": "report",
    "resolve": "resolve",
    "حسم": "resolve",
    "تحديث": "resolve",
    "🔄": "resolve",
}

# The persistent panel, pinned under the chat. A label alone sends its text,
# which ``_resolve`` maps back to an action; the leading emoji is the anchor.
REPLY_MENU: list[list[str]] = [
    ["🤖 تحليل AYZR", "📒 الدفتر"],
    ["🔄 تحديث"],
]


def _reply_keyboard() -> dict:
    return {
        "keyboard": [[{"text": label} for label in row] for row in REPLY_MENU],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def _resolve(text: str) -> str:
    """Map typed text or a panel label to a canonical action.

    Telegram delivers a typed command with a leading slash (``/ayzr``), and a
    panel label may carry an emoji head (``📒 الدفتر``) plus, or minus, the
    variation selector. All three forms are normalised before the raw text is
    handed back as unknown.
    """
    key = (text or "").strip()
    if not key:
        return key
    key = key.lstrip("/")
    if key in _COMMANDS:
        return _COMMANDS[key]
    head = key.split(" ", 1)[0]
    if head in _COMMANDS:
        return _COMMANDS[head]
    stripped = key.replace("\ufe0f", "").strip()
    return _COMMANDS.get(stripped, key)


class AyzrBot:
    """AYZR's standalone loop: its own command offset, its own runner."""

    def __init__(
        self,
        settings: AyzrSettings | None = None,
        engine: Engine | None = None,
        tg: Telegram | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self.settings = settings or AyzrSettings()
        self.engine = engine or Engine(self.settings)
        self.tg = tg or (
            Telegram(self.settings.telegram_token) if self.settings.telegram_token else None
        )
        self.chat_id = self.settings.telegram_chat
        self.state_path = Path(state_path or self.settings.state_path)
        self._offset: int | None = None
        self._started_at = time.time()
        self._load()

    # ── state ────────────────────────────────────────────────────────────

    def _load(self) -> None:
        """Read the command offset so a restart does not replay old updates."""
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._offset = raw.get("offset")
        except (OSError, ValueError):
            self._offset = None

    def _save(self) -> None:
        """Persist the offset. A failure is logged, never fatal."""
        with contextlib.suppress(OSError):
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps({"offset": self._offset}), encoding="utf-8")

    # ── transport guard ──────────────────────────────────────────────────

    def _authorized(self, chat_id: str) -> bool:
        """Whether this chat is AYZR's own configured chat.

        With no configured chat every chat is refused rather than admitted, so
        a missing setting cannot become an open engine that spends the day's
        quota for whoever finds the bot.
        """
        allowed = str(self.chat_id or "").strip()
        return bool(allowed) and str(chat_id).strip() == allowed

    def _log(self, message: str) -> None:
        log.info("%s", message)

    # ── loop ─────────────────────────────────────────────────────────────

    def run_forever(self, *, open_health: bool = True, start_runner: bool = True) -> None:
        """Poll commands, keep the shadow runner ticking, stay alive.

        The bot's presence is what makes the month-long test *visible*: it
        starts the runner, opens its own health port, and answers commands. If
        it has no token it still runs the runner headless, because the
        collection matters more than the buttons.

        ``open_health`` and ``start_runner`` let a supervisor that runs this loop
        beside the main bot (see :mod:`goldbot.dual`) hand the port and the
        runner to that supervisor instead of racing it for either. Alone, both
        default on.
        """
        if open_health:
            self._start_health()
        if start_runner:
            self._start_runner()
        if self.tg is not None:
            self._register_commands()
            self._install_panel()
        else:
            self._log("AYZR: لا توكن تيليجرام — يعمل بلا واجهة، الدفتر فقط")
        self._log(f"AYZR يعمل — كل {self.settings.poll_seconds} ثانية")
        long_poll = max(1, min(30, int(self.settings.poll_seconds)))
        while True:
            if self.tg is None:
                time.sleep(max(1.0, float(self.settings.poll_seconds)))
                continue
            try:
                self.handle_updates(timeout=long_poll)
            except TelegramError as exc:
                self._log(f"AYZR تحديثات: فشل ({exc})")
                time.sleep(2)

    def handle_updates(self, timeout: int = 0) -> None:
        """Drain AYZR's updates. Only its own chat is served."""
        if self.tg is None:
            return
        updates = self.tg.updates(self._offset, timeout=timeout)
        for update in updates:
            self._offset = update.get("update_id", 0) + 1
            callback = update.get("callback_query")
            if callback:
                chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id", ""))
                if not self._authorized(chat_id):
                    self._log(f"AYZR: تجاهل ضغطة من محادثة غير مصرّحة ({chat_id or '؟'})")
                    continue
                self.tg.answer_callback(str(callback.get("id", "")))
                self._respond(chat_id, str(callback.get("data", "")))
                continue
            message = update.get("message") or update.get("edited_message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            if not self._authorized(chat_id):
                self._log(f"AYZR: تجاهل رسالة من محادثة غير مصرّحة ({chat_id})")
                continue
            self._respond(chat_id, text)
        self._save()

    def _respond(self, chat_id: str, raw: str) -> None:
        """Render one AYZR screen for a command or a button."""
        if self.tg is None:
            return
        action = _resolve(raw)
        self._log(f"AYZR طلب: {action}")
        try:
            if action == "report":
                body = format_report(self.engine.report())
                self.tg.send(chat_id, body, keyboard=menu_keyboard())
                return
            if action == "resolve":
                n = self.engine.resolve_only()
                body = format_report(self.engine.report()) + f"\n\n_حُسمت {n} إشارة من الشريط._"
                self.tg.send(chat_id, body, keyboard=menu_keyboard())
                return
            if action == "help":
                self.tg.send(chat_id, HELP, keyboard=menu_keyboard())
                self.tg.send(chat_id, "⌨️", markdown=False, keyboard=_reply_keyboard())
                return
            if action == "analyse":
                self.tg.typing(chat_id)
                run = self.engine.run(with_ai=True)
                if not run.read.ok:
                    detail = "؛ ".join(run.read.errors[:3]) or run.error or "لا قراءة"
                    self.tg.send(chat_id, f"⚠️ *AYZR:* تعذّر قراءة السوق.\n_{detail}_")
                    return
                self.tg.send(
                    chat_id, format_decision(run.read, run.decision), keyboard=menu_keyboard()
                )
                return
            self.tg.send(
                chat_id, "أمر غير معروف. اضغط زراً من الأزرار تحت.", keyboard=menu_keyboard()
            )
        except TelegramError:
            pass

    def _register_commands(self) -> None:
        """Publish AYZR's own ``/`` menu. Failure is cosmetic, never fatal."""
        if self.tg is None:
            return
        with contextlib.suppress(TelegramError):
            self.tg.set_commands(
                [
                    ("start", "AYZR — القائمة والمساعدة"),
                    ("ayzr", "قراءة AYZR الكاملة"),
                    ("دفتري", "دفتر نتائج AYZR"),
                    ("resolve", "حسم الإشارات المفتوحة"),
                ]
            )

    def _install_panel(self) -> None:
        """Pin AYZR's panel under its own chat."""
        if self.tg is None or not str(self.chat_id or "").strip():
            return
        with contextlib.suppress(TelegramError):
            self.tg.send(self.chat_id, "⌨️ AYZR", markdown=False, keyboard=_reply_keyboard())

    def _start_runner(self) -> None:
        """Start the shadow runner, if and only if it was asked for."""
        if not self.settings.enabled:
            self._log("AYZR: العلم AYZR_ENABLED غير مفعّل — بلا دورة تلقائية")
            return
        try:
            runner = ShadowRunner(self.settings)
            runner.start_thread()
            self._log(f"AYZR: الدورة التلقائية تعمل كل {runner.interval} ثانية")
        except Exception as exc:  # the experiment must never break the service
            self._log(f"AYZR: تعذّر بدء الدورة ({type(exc).__name__}: {exc})")

    def _start_health(self) -> None:
        """Open AYZR's own health port when the host provides one."""
        raw = os.environ.get("PORT", "").strip()
        if not raw:
            return
        try:
            port = int(raw)
        except ValueError:
            self._log(f"AYZR: PORT غير صالح ({raw!r})")
            return
        from ..health import SELF_PING_SECONDS, start_health_server, start_self_ping

        start_health_server(port, self.health_status)
        self._log(f"AYZR: منفذ الصحة على {port}")
        external = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
        if external:
            start_self_ping(external)
            self._log(f"AYZR: منبّه ذاتي كل {int(SELF_PING_SECONDS)} ثانية ({external})")

    def health_status(self) -> dict:
        """AYZR's own health document — deliberately not the bot's.

        A shadow test that shares an endpoint with the bot it shadows cannot be
        told apart when one of them is down, which is the one thing a health
        check is for.
        """
        return {
            "status": "ok",
            "service": "ayzr",
            "uptime_seconds": round(time.time() - self._started_at),
            "poll_seconds": self.settings.poll_seconds,
            "ai_enabled": self.settings.ai_enabled,
            "runner_enabled": self.settings.enabled,
            "telegram": bool(self.tg is not None),
        }


__all__ = ["HELP", "REPLY_MENU", "AyzrBot"]
