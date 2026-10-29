"""The command surface: which command renders which screen.

This is the dispatcher half of the bot. It is a mixin rather than a standalone
class because a screen is rendered from the *bot's* live state — the current
settings, the pipeline to read, the chat to answer — so it belongs to the same
object. Keeping it here instead of in ``bot.py`` splits the two jobs that file
used to carry: rendering a screen, and running the loop that feeds it.
"""

from __future__ import annotations

from ..config import Settings
from ..pipeline import Pipeline, Result
from .client import Telegram, TelegramError
from .format import format_analysis, format_news, format_plan
from .menus import (
    ALL_TIMEFRAMES,
    HELP,
    PANEL_HINT,
    SENSITIVITY,
    menu_keyboard,
    reply_keyboard,
    resolve_data,
    settings_keyboard,
)

# Typed aliases a trader might reach for, in Arabic and English. The command is
# lower-cased and stripped of its leading slash before lookup.
_COMMAND_ALIASES = {
    "تحليل": "analyse",
    "analysis": "analyse",
    "ta": "analyse",
    "صفقة": "plan",
    "trade": "plan",
    "signal": "plan",
    "أخبار": "news",
    "اخبار": "news",
    "news": "news",
    "حالة": "status",
    "status": "status",
    "تحديث": "refresh",
    "refresh": "refresh",
    "reload": "refresh",
    "إعدادات": "settings",
    "اعدادات": "settings",
    "settings": "settings",
    "menu": "menu",
    "قائمة": "menu",
    # AYZR's own commands, typed or tapped from its buttons.
    "ayzr": "ayzr:analyse",
    "🤖": "ayzr:analyse",
    "📒": "ayzr:report",
    "دفتر": "ayzr:report",
}


class CommandScreens:
    """Renders one screen per command. Mixed into :class:`~.bot.Bot`.

    The host class supplies the attributes and methods this needs — the live
    settings, the pipeline to read, the chat transport, and the state hook that
    records a preference. They are declared here so a type checker sees the
    contract the mixin relies on, and so the dependency is explicit rather than
    an ``# type: ignore`` on every use.
    """

    settings: Settings
    pipeline: Pipeline
    tg: Telegram

    def _log(self, message: str) -> None:  # pragma: no cover - provided by the host
        raise NotImplementedError

    def _reconfigure(self, **overrides: object) -> None:  # pragma: no cover - host
        raise NotImplementedError

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

        # AYZR is its own engine on its own budget. It is reached only through
        # its namespace, and imported lazily so the bot's own start-up never
        # pays for it — pressing its button is the only thing that loads it.
        if data.startswith("ayzr:"):
            if callback_id:
                self.tg.answer_callback(callback_id)
            self.tg.typing(chat_id)
            try:
                from ..ayzr.panel import handle as ayzr_handle

                body = ayzr_handle(data)
            except Exception as exc:  # AYZR must never take the bot down
                body = f"⚠️ *AYZR:* تعذّر العرض ({type(exc).__name__})."
            self.tg.send(chat_id, body, keyboard=menu_keyboard())
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
                self.tg.send(chat_id, PANEL_HINT, markdown=False, keyboard=reply_keyboard())
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
            lines.append(
                f"تحقق السعر: فرق {result.spot.get('max_divergence_pct')}% "
                f"({'موثوق' if result.spot.get('trusted') else 'مشكوك'})"
            )
        if result.errors:
            lines.append(f"أخطاء: {len(result.errors)}")
        return "\n".join(lines)

    def _respond(self, chat_id: str, text: str) -> None:
        command = text.split()[0].lower().lstrip("/")
        self._answer(chat_id, resolve_data(_COMMAND_ALIASES.get(command, command)))
