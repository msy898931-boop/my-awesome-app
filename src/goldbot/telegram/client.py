"""A tiny Telegram Bot API client — no SDK, no dependency.

Only four calls are needed: send a message, read updates, answer a callback,
and identify the bot. Keeping them here means the project installs with no
third-party packages at all, which is what "free and keyless" has to mean in
practice.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 20.0


class TelegramError(RuntimeError):
    """The Bot API refused the call. Carries the description."""


class Telegram:
    """Minimal Bot API wrapper. One instance per token."""

    def __init__(self, token: str, timeout: float = TIMEOUT) -> None:
        if not token:
            raise TelegramError("no bot token configured")
        self.token = token
        self.timeout = timeout

    def _call(self, method: str, payload: dict | None = None) -> dict:
        url = API.format(token=self.token, method=method)
        data = json.dumps(payload or {}).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise TelegramError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TelegramError(f"network: {exc}") from exc
        if not body.get("ok"):
            raise TelegramError(body.get("description", "unknown error"))
        return body.get("result", {})

    def me(self) -> dict:
        return self._call("getMe")

    def send(self, chat_id: str, text: str, markdown: bool = True,
             keyboard: dict | None = None) -> bool:
        """Send a message, splitting on Telegram's 4096-character limit.

        A long analysis is sent in parts rather than truncated, because a
        cut-off explanation is worse than two messages. When a keyboard is
        given it is attached to the *last* part only, so the buttons land under
        the final line instead of repeating after every chunk.
        """
        if not str(chat_id or "").strip():
            # Telegram answers this with a bare 400 and no clue; failing here
            # names the missing setting instead.
            raise TelegramError("no chat id configured (TELEGRAM_CHAT_ID)")
        if not str(text or "").strip():
            raise TelegramError("refusing to send an empty message")
        parts = _chunks(text, 4000)
        ok = True
        for index, chunk in enumerate(parts):
            payload: dict = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if markdown:
                payload["parse_mode"] = "Markdown"
            if keyboard and index == len(parts) - 1:
                payload["reply_markup"] = keyboard
            try:
                self._call("sendMessage", payload)
            except TelegramError:
                # A malformed entity is the usual cause; resend as plain text.
                payload.pop("parse_mode", None)
                try:
                    self._call("sendMessage", payload)
                except TelegramError:
                    ok = False
        return ok

    def answer_callback(self, callback_id: str, text: str = "") -> bool:
        """Clear the spinner on a pressed button.

        Telegram keeps the button spinning until this lands, so it is called
        the moment a callback arrives — before any work the answer needs.
        """
        if not str(callback_id or "").strip():
            return False
        payload: dict = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
        try:
            self._call("answerCallbackQuery", payload)
            return True
        except TelegramError:
            return False

    def typing(self, chat_id: str) -> bool:
        """Show the typing indicator, so a slow read still feels alive."""
        if not str(chat_id or "").strip():
            return False
        try:
            self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
            return True
        except TelegramError:
            return False

    def set_commands(self, commands: list[tuple[str, str]]) -> bool:
        """Publish the ``/`` command menu for the default scope."""
        if not commands:
            return False
        payload = {
            "commands": [{"command": c, "description": d} for c, d in commands]
        }
        try:
            self._call("setMyCommands", payload)
            return True
        except TelegramError:
            return False

    def updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        payload: dict = {"timeout": timeout}
        if offset is not None:
            payload["offset"] = offset
        try:
            result = self._call("getUpdates", payload)
        except TelegramError:
            return []
        return result if isinstance(result, list) else []


def _chunks(text: str, size: int) -> list[str]:
    """Split into pieces Telegram will accept.

    A single line longer than ``size`` used to leave an empty piece in the
    list, and an empty message is a 400 from Telegram — so one long line could
    sink an otherwise fine send. Overlong lines are now hard-split, and empty
    pieces are never emitted.
    """
    if size <= 0:
        return [text]
    if len(text) <= size:
        return [text] if text else []

    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        while len(line) > size:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:size])
            line = line[size:]
        if len(current) + len(line) > size:
            if current:
                parts.append(current)
            current = line
        else:
            current += line
    if current:
        parts.append(current)
    return parts
