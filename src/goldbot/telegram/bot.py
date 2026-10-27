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
    "الأوامر المتاحة:\n"
    "/start أو /help — هذه القائمة\n"
    "/تحليل — قراءة SMC/ICT الكاملة لكل فريم\n"
    "/صفقة — آخر صفقة أو حالة السوق الآن\n"
    "/أخبار — التقويم الاقتصادي والعناوين\n"
    "/حالة — ملخّص سريع\n\n"
    "البوت يفحص السوق كل فترة ويرسل عند ظهور فكرة جديدة.\n"
    "تحليل استشاري فقط — لا ينفّذ أي صفقة."
)


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
            self.tg.send(self.chat_id, text)
        return result

    # ── commands ─────────────────────────────────────────────────────────

    def handle_updates(self) -> None:
        updates = self.tg.updates(self._last_offset)
        for update in updates:
            self._last_offset = update.get("update_id", 0) + 1
            message = update.get("message") or update.get("edited_message") or {}
            text = (message.get("text") or "").strip()
            chat_id = str((message.get("chat") or {}).get("id", ""))
            if not text or not chat_id:
                continue
            self._respond(chat_id, text)
        self._save()

    def _respond(self, chat_id: str, text: str) -> None:
        command = text.split()[0].lower().lstrip("/")
        try:
            if command in {"start", "help", "مساعدة"}:
                self.tg.send(chat_id, HELP)
            elif command in {"تحليل", "analysis", "ta"}:
                result = self.pipeline.run(with_ai=False)
                self.tg.send(chat_id, format_analysis(result.facts, result.news))
            elif command in {"صفقة", "trade", "signal"}:
                result = self.pipeline.run(with_ai=True)
                self.tg.send(chat_id, format_plan(result.plan, result.analysis, result.news))
            elif command in {"أخبار", "اخبار", "news"}:
                result = self.pipeline.run(with_ai=False)
                self.tg.send(chat_id, format_news(result.news))
            elif command in {"حالة", "status"}:
                result = self.pipeline.run(with_ai=False)
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
                self.tg.send(chat_id, "\n".join(lines))
            else:
                self.tg.send(chat_id, "أمر غير معروف. أرسل /help للقائمة.")
        except TelegramError:
            pass

    # ── loop ─────────────────────────────────────────────────────────────

    def run_forever(self) -> None:
        """Poll commands, then the market, then sleep. Runs until interrupted."""
        while True:
            try:
                self.handle_updates()
            except TelegramError:
                pass
            try:
                self.tick()
            except Exception:
                # A bad cycle must not kill the loop; the next one may be fine.
                pass
            time.sleep(self.settings.poll_seconds)
