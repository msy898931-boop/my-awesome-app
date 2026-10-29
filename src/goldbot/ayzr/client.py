"""AYZR's own model client.

This deliberately does **not** reuse ``goldbot.ai.analyst``. Two reasons, both
about isolation:

  * the main analyst's daily cooldowns live on an instance built for the bot's
    lifetime; sharing it would let an AYZR call park a model for the *bot*, and
    vice versa;
  * AYZR keeps its own per-day counter on its own disk file, so a day of AYZR
    analysis can never quietly consume the bot's allowance.

What it *does* reuse is the transport discipline learned the hard way and now
proven: a browser-like User-Agent (Groq sits behind Cloudflare, which 403s
urllib's default UA), the two kinds of 429 told apart (per-minute is retried,
per-day parks the model), and the error body cached because ``HTTPError.read()``
drains the stream.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.error
import urllib.request

from ..net import open_url
from .config import AyzrSettings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = 60.0
RETRY_BACKOFF = 20.0
DAILY_COOLDOWN = 30 * 60.0
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class AiBudgetError(RuntimeError):
    """Raised when AYZR's own daily cap, or every model's quota, is spent."""


class AiClient:
    """Calls the model chain on AYZR's budget, with its own day counter."""

    def __init__(self, settings: AyzrSettings) -> None:
        self.settings = settings
        self._cooldowns: dict[str, float] = {}
        self._load_state()

    # ── AYZR's own quota, on AYZR's own file ─────────────────────────────

    def _load_state(self) -> None:
        self._day = ""
        self._calls = 0
        try:
            state = json.loads(self.settings.ai_state_path.read_text(encoding="utf-8"))
            self._day = str(state.get("day") or "")
            self._calls = int(state.get("calls") or 0)
        except (OSError, ValueError):
            pass

    def _save_state(self) -> None:
        self.settings.ensure_dirs()
        _atomic_write(self.settings.ai_state_path, {"day": self._day, "calls": self._calls})

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime())

    def calls_today(self) -> int:
        if self._day != self._today():
            self._day = self._today()
            self._calls = 0
            self._save_state()
        return self._calls

    def _spend(self) -> None:
        """Record one call against today, raising once the cap is reached."""
        if self.calls_today() >= self.settings.ai_daily_cap:
            raise AiBudgetError(
                f"بلغ AYZR سقفه اليومي ({self.settings.ai_daily_cap} نداء) — يُستأنف غداً"
            )
        self._day = self._today()
        self._calls += 1
        self._save_state()

    def remaining_today(self) -> int:
        return max(0, self.settings.ai_daily_cap - self.calls_today())

    # ── the call ─────────────────────────────────────────────────────────

    def complete(self, system: str, user: str) -> tuple[str, str]:
        """Return ``(reply, model)`` from the first model that answers."""
        if not self.settings.ai_enabled:
            raise AiBudgetError("لا يوجد مفتاح ذكاء اصطناعي لـ AYZR")
        self._spend()

        models = [m for m in self._models() if not self._parked(m)]
        if not models:
            raise AiBudgetError(self._parked_reason())

        last_error: Exception | None = None
        for attempt in range(2):
            for model in models:
                try:
                    return self._one(model, system, user), model
                except urllib.error.HTTPError as exc:
                    detail, code = _http_reason(exc)
                    if exc.code == 429:
                        if code == "rate_limit_exceeded" and _is_daily(exc):
                            self._park(model, exc)
                            last_error = AiBudgetError(f"{model}: نفدت الحصة اليومية ({detail})")
                        else:
                            last_error = AiBudgetError(f"{model}: تجاوز حد الاستخدام ({detail})")
                        continue
                    if code == "json_validate_failed":
                        last_error = AiBudgetError(f"{model}: أخرج JSON غير صالح ({detail})")
                        continue
                    raise AiBudgetError(f"{model}: {detail}") from exc
                except Exception as exc:
                    last_error = AiBudgetError(f"{model}: {type(exc).__name__}: {exc}")
            if attempt == 0:
                time.sleep(RETRY_BACKOFF)
        raise last_error or AiBudgetError("لا يوجد موديل متاح")

    def _models(self) -> list[str]:
        chain = [self.settings.ai_model, *self.settings.ai_fallbacks]
        seen: list[str] = []
        for m in chain:
            if m and m not in seen:
                seen.append(m)
        return seen

    def _one(self, model: str, system: str, user: str) -> str:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.25,
                "max_tokens": self.settings.ai_max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode()
        req = urllib.request.Request(
            GROQ_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.ai_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            method="POST",
        )
        with open_url(req, timeout=TIMEOUT) as resp:
            payload = json.loads(resp.read())
        choices = payload.get("choices") or []
        if not choices:
            raise RuntimeError("no choices in response")
        content = choices[0]["message"].get("content") or ""
        if not content.strip():
            raise RuntimeError("empty content (token budget exhausted)")
        return content

    def _parked(self, model: str) -> bool:
        until = self._cooldowns.get(model, 0.0)
        if until <= time.time():
            self._cooldowns.pop(model, None)
            return False
        return True

    def _park(self, model: str, exc: urllib.error.HTTPError) -> None:
        wait = _parse_retry_seconds(exc) or DAILY_COOLDOWN
        self._cooldowns[model] = time.time() + wait

    def _parked_reason(self) -> str:
        soonest = min(self._cooldowns.values()) if self._cooldowns else 0.0
        mins = max(1, int((soonest - time.time()) / 60)) if soonest else 0
        return f"كل موديلات AYZR نفدت حصتها — تُستأنف بعد ~{mins} دقيقة"


def parse_json(text: str) -> dict | None:
    """Parse a model reply that *should* be JSON, tolerating a fenced block."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except ValueError:
        pass
    # A model occasionally wraps the object in a fenced block despite the
    # instruction. Recover the outermost object rather than discarding a read.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        with contextlib.suppress(ValueError):
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
    return None


def _atomic_write(path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _error_payload(exc: urllib.error.HTTPError) -> dict:
    """The parsed error body, read at most once (the stream drains on read)."""
    cached = getattr(exc, "_ayzr_body", None)
    if cached is not None:
        return cached
    try:
        payload = json.loads(exc.read())
        payload = payload if isinstance(payload, dict) else {}
    except Exception:
        payload = {}
    with contextlib.suppress(Exception):
        exc._ayzr_body = payload  # type: ignore[attr-defined]
    return payload


def _http_reason(exc: urllib.error.HTTPError) -> tuple[str, str]:
    err = _error_payload(exc).get("error") or {}
    code = str(err.get("code") or "")
    message = str(err.get("message") or "")
    if code == "rate_limit_exceeded":
        short = message.split("\n")[0][:160]
        return (short or "تجاوز حد الاستخدام"), code
    return (message or f"HTTP {exc.code}"), code or f"HTTP {exc.code}"


def _is_daily(exc: urllib.error.HTTPError) -> bool:
    """Whether a 429 is a *per-day* limit (which waiting cannot clear)."""
    err = _error_payload(exc).get("error") or {}
    text = json.dumps(err, ensure_ascii=False).lower()
    return any(k in text for k in ("per day", "per-day", "daily", "tpd", "day"))


def _parse_retry_seconds(exc: urllib.error.HTTPError) -> float | None:
    """Groq's named wait, in seconds, when it states one."""
    err = _error_payload(exc).get("error") or {}
    text = json.dumps(err, ensure_ascii=False)
    m = re.search(r"try again in ([\d.]+)s", text) or re.search(r"([\d.]+)s", text)
    if m:
        with contextlib.suppress(ValueError):
            return float(m.group(1))
    return None
