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

    def send(self, chat_id: str, text: str, markdown: bool = True) -> bool:
        """Send a message, splitting on Telegram's 4096-character limit.

        A long analysis is sent in parts rather than truncated, because a
        cut-off explanation is worse than two messages.
        """
        if not str(chat_id or "").strip():
            # Telegram answers this with a bare 400 and no clue; failing here
            # names the missing setting instead.
            raise TelegramError("no chat id configured (TELEGRAM_CHAT_ID)")
        if not str(text or "").strip():
            raise TelegramError("refusing to send an empty message")
        ok = True
        for chunk in _chunks(text, 4000):
            payload: dict = {"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True}
            if markdown:
                payload["parse_mode"] = "Markdown"
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
