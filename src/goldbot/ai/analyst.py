"""The analyst: calls the model, validates its reply, and never trusts it blindly.

Two layers of defence sit between the model and the reader:

  * **Transport** — a timeout, a single retry, and a hard failure that degrades
    to the deterministic fallback rather than raising.
  * **Coherence** — the returned plan is checked against the facts. A stop on
    the wrong side of entry, targets out of travel order, or an R:R below the
    methodology's minimum causes the plan to be dropped *with the reason
    recorded*, never silently repaired.

The result always carries ``configured`` and ``validated`` flags so a caller
can tell "the model said so" from "the code measured it".
"""

from __future__ import annotations

import contextlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field

from ..config import Settings
from ..net import open_url
from .prompt import SYSTEM_PROMPT, build_prompt

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
TIMEOUT = 45.0
# The free tier meters 8000 tokens per minute across models, and a full fact
# sheet plus a reasoning reply lands near that. One short wait clears the
# window rather than failing the run.
RETRY_BACKOFF = 20.0
# A per-*day* limit is not cleared by waiting 20 seconds. Once a model reports
# one, every further call is a guaranteed 429, so the model is parked until the
# window it named has passed. ``_parse_retry_seconds`` reads the duration out of
# Groq's message; this is the fallback when it cannot be parsed.
DAILY_COOLDOWN = 30 * 60.0
# Groq sits behind Cloudflare, which rejects urllib's default User-Agent *and*
# a custom bot string with a 403 (error 1010). Only a browser-like UA passes.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class AnalysisResult:
    """What the analyst produced, and how much of it is trustworthy."""

    configured: bool = False
    validated: bool = False
    market_state: str = ""
    bias: str = "neutral"
    setup: str | None = None
    reasoning: str = ""
    confidence: float = 0.0
    trade: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class Analyst:
    """Wraps a chat model with the persona, validation, and a fallback."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        # model -> epoch when its daily cooldown expires. Lives on the instance,
        # and the bot builds one Analyst for its lifetime, so a parked model
        # stays parked across ticks instead of being retried every minute.
        self._cooldowns: dict[str, float] = {}

    def analyse(
        self, facts: dict, news: dict | None = None, min_rr: float | None = None
    ) -> AnalysisResult:
        if not self.settings.ai_enabled:
            return self._fallback(facts, "لم يُضبط مفتاح الذكاء الاصطناعي")
        try:
            reply = self._call(facts, news)
        except Exception as exc:  # transport must never take the engine down
            return self._fallback(facts, f"تعذّر الوصول للنموذج: {exc}")

        parsed = _parse_json(reply)
        if parsed is None:
            return self._fallback(facts, "ردّ النموذج لم يكن JSON صالحاً")

        result = AnalysisResult(
            configured=True,
            market_state=str(parsed.get("market_state", "")).strip(),
            bias=_norm_bias(parsed.get("bias")),
            setup=parsed.get("setup") or None,
            reasoning=str(parsed.get("reasoning", "")).strip(),
            confidence=_clamp(parsed.get("confidence"), 0.0, 1.0),
            warnings=[str(w) for w in (parsed.get("warnings") or [])][:6],
        )
        trade = parsed.get("trade") or {}
        min_rr = min_rr if min_rr is not None else self.settings.min_rr
        result.trade, result.dropped = _validate_trade(trade, min_rr)
        result.validated = True
        return result

    def _models(self) -> list[str]:
        chain = [self.settings.groq_model, *self.settings.groq_fallbacks]
        seen: list[str] = []
        for m in chain:
            if m and m not in seen:
                seen.append(m)
        return seen

    def _call(self, facts: dict, news: dict | None) -> str:
        """Ask each model in the chain until one answers.

        Groq meters quota per model, so a spent budget on the primary is not
        fatal: the next model in the chain is tried. Two kinds of 429 are
        distinguished, because they need opposite responses:

          * **per-minute** — waiting clears it, so one backed-off retry is made;
          * **per-day** — waiting 20s does nothing. The model is parked until the
            window it named has passed, and the loop stops re-trying it. Retrying
            a daily limit every minute is how a bot burns a day's quota in an
            hour and then looks broken for the rest of the day.
        """
        prompt = build_prompt(facts, news)
        models = [m for m in self._models() if not self._parked(m)]
        if not models:
            raise RuntimeError(self._parked_reason())

        last_error: Exception | None = None
        for attempt in range(2):
            for model in models:
                try:
                    return self._call_model(model, prompt)
                except urllib.error.HTTPError as exc:
                    detail, code = _http_reason(exc)
                    if exc.code == 429:
                        if code == "rate_limit_exceeded" and _is_daily(exc):
                            self._park(model, exc)
                            last_error = RuntimeError(f"{model}: نفدت الحصة اليومية ({detail})")
                        else:
                            last_error = RuntimeError(f"{model}: تجاوز حد الاستخدام ({detail})")
                        continue  # a different model has its own quota
                    if code == "json_validate_failed":
                        # The model produced malformed JSON — usually a reasoning
                        # model whose scratchpad ate the token budget, not a fault
                        # in the request. Another model may answer cleanly, so the
                        # chain advances rather than abandoning every model behind
                        # it. This is why a single bad generation used to look like
                        # "the AI is down": the primary spent its daily quota, the
                        # fallback returned bad JSON, and the raise ended the chain.
                        last_error = RuntimeError(f"{model}: أخرج JSON غير صالح ({detail})")
                        continue
                    raise RuntimeError(f"{model}: {detail}") from exc
                except Exception as exc:
                    last_error = RuntimeError(f"{model}: {type(exc).__name__}: {exc}")
            if attempt == 0:
                time.sleep(RETRY_BACKOFF)
        raise last_error or RuntimeError("no model available")

    def _parked(self, model: str) -> bool:
        """Whether a model is inside a cooldown set by an earlier daily limit."""
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
        return f"كل الموديلات نفدت حصتها اليومية — تُستأنف بعد ~{mins} دقيقة"

    def _call_model(self, model: str, prompt: str) -> str:
        body = json.dumps(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
                "max_tokens": self.settings.groq_max_tokens,
                "response_format": {"type": "json_object"},
            }
        ).encode()
        req = urllib.request.Request(
            GROQ_URL,
            data=body,
            headers={
                "Authorization": f"Bearer {self.settings.groq_key}",
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
            # A reasoning model can burn the whole budget on its scratchpad and
            # return nothing; that is a failure, not an empty answer.
            raise RuntimeError("empty content (token budget exhausted)")
        return content

    def _fallback(self, facts: dict, reason: str) -> AnalysisResult:
        """A deterministic Arabic read, built only from the facts.

        This is what the bot shows when there is no key or the model is down:
        a real summary of real structure, with no invented number and no
        fabricated confidence. ``confidence`` stays 0 so the card omits it —
        frame agreement is not a probability of success, and labelling it as
        one put a fake "61%" next to a real "3 of 5 frames" saying the same
        number twice.
        """
        confluence = facts.get("confluence", {})
        frames = facts.get("timeframes", {})
        bias = confluence.get("bias", "neutral")
        lines = [
            f"قراءة الحسابات (بدون ذكاء اصطناعي): {reason}",
            f"الانحياز العام: {bias} — توافق {confluence.get('bullish_frames', 0)} صاعد / "
            f"{confluence.get('bearish_frames', 0)} هابط من {confluence.get('frames_read', 0)} فريم.",
        ]
        for tf, f in frames.items():
            pd_zone = (f.get("premium_discount") or {}).get("zone")
            event = f.get("last_event") or {}
            lines.append(
                f"{tf}: اتجاه {f.get('trend')} — آخر حدث {event.get('type', '—')} "
                f"{event.get('direction', '')} — منطقة {pd_zone or '—'} — ATR {f.get('atr')}"
            )
        return AnalysisResult(
            configured=False,
            validated=False,
            market_state="\n".join(lines),
            bias=bias,
            confidence=0.0,
            warnings=["هذه قراءة حسابية فقط، بلا تحليل ذكاء اصطناعي"],
            error=reason,
        )


def _error_payload(exc: urllib.error.HTTPError) -> dict:
    """The parsed error body, read at most once.

    ``HTTPError.read()`` drains the underlying stream: a second call returns
    ``b""``. Since three helpers want the body, it is cached on the exception
    so whichever runs first does not starve the others.
    """
    cached = getattr(exc, "_goldbot_body", None)
    if cached is not None:
        return cached
    try:
        payload = json.loads(exc.read())
        payload = payload if isinstance(payload, dict) else {}
    except Exception:
        payload = {}
    # Best-effort cache on the exception object; some HTTPError subclasses
    # forbid attribute assignment, and losing the cache is not fatal.
    with contextlib.suppress(Exception):
        exc._goldbot_body = payload  # type: ignore[attr-defined]
    return payload


def _http_reason(exc: urllib.error.HTTPError) -> tuple[str, str]:
    """Groq's own error code and a readable reason, from the response body.

    Returns ``(detail, code)``. The code is what the caller branches on; the
    detail is what a human reads. Dropping the body is what made a spent daily
    quota surface as a bare ``RuntimeError`` with no way to tell it from a
    network blip.
    """
    err = _error_payload(exc).get("error") or {}
    code = str(err.get("code") or "")
    message = str(err.get("message") or "")
    if code == "rate_limit_exceeded":
        return _short_rate_message(message), code
    return (message or f"HTTP {exc.code}"), code or f"HTTP {exc.code}"


def _short_rate_message(message: str) -> str:
    """Condense Groq's rate-limit prose to the limit and the wait.

    The raw message is several hundred characters of billing upsell; what a
    reader needs is which ceiling was hit and when it lifts.
    """
    low = message.lower()
    kind = (
        "الحصة اليومية" if "per day" in low else ("حد الدقيقة" if "per minute" in low else "الحد")
    )
    wait = ""
    match = re.search(r"try again in ([0-9.]+[a-z]+(?:[0-9.]+[a-z]+)?)", low)
    if match:
        wait = f"، يعود بعد {match.group(1)}"
    return f"{kind}{wait}"


def _is_daily(exc: urllib.error.HTTPError) -> bool:
    """Whether a 429 named a per-day ceiling rather than a per-minute one."""
    message = str((_error_payload(exc).get("error") or {}).get("message", "")).lower()
    return "per day" in message or "tpd" in message


def _parse_retry_seconds(exc: urllib.error.HTTPError) -> float:
    """The wait Groq named in 'try again in 20m0.96s', in seconds (0 if absent)."""
    message = str((_error_payload(exc).get("error") or {}).get("message", ""))
    match = re.search(r"try again in ([0-9]+)m([0-9.]+)s", message)
    if match:
        return int(match.group(1)) * 60 + float(match.group(2))
    match = re.search(r"try again in ([0-9.]+)s", message)
    if match:
        return float(match.group(1))
    return 0.0


def _parse_json(text: str) -> dict | None:
    """Parse the model's reply, tolerating a fenced code block around it."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.lstrip("json").strip()
    try:
        data = json.loads(cleaned)
    except ValueError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(cleaned[start : end + 1])
        except ValueError:
            return None
    return data if isinstance(data, dict) else None


def _norm_bias(value) -> str:
    low = str(value or "").strip().lower()
    return low if low in {"bullish", "bearish", "neutral"} else "neutral"


def _clamp(value, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _validate_trade(trade: dict, min_rr: float) -> tuple[dict, list[str]]:
    """Check a proposed plan for coherence; drop it whole if it fails.

    A dropped plan is returned empty with every reason listed. Repairing it
    silently would put a number in front of the reader that no one chose.
    """
    direction = str(trade.get("direction", "none")).strip().lower()
    if direction not in {"buy", "sell"}:
        return {"direction": "none"}, []

    raw = (trade.get("entry"), trade.get("stop"), trade.get("target"))
    if any(v is None or v == "" for v in raw):
        return {"direction": "none"}, ["الصفقة بلا أسعار صالحة — أُسقطت"]
    try:
        entry = float(str(raw[0]))
        stop = float(str(raw[1]))
        target = float(str(raw[2]))
    except (TypeError, ValueError):
        return {"direction": "none"}, ["الصفقة بلا أسعار صالحة — أُسقطت"]

    reasons: list[str] = []
    if direction == "buy":
        if not stop < entry:
            reasons.append("الوقف ليس أسفل الدخول في صفقة شراء")
        if not target > entry:
            reasons.append("الهدف ليس أعلى الدخول في صفقة شراء")
    else:
        if not stop > entry:
            reasons.append("الوقف ليس أعلى الدخول في صفقة بيع")
        if not target < entry:
            reasons.append("الهدف ليس أسفل الدخول في صفقة بيع")

    risk = abs(entry - stop)
    reward = abs(target - entry)
    rr = round(reward / risk, 2) if risk > 0 else 0.0
    if rr < min_rr:
        reasons.append(f"نسبة المخاطرة/المكافأة {rr} أقل من الحد {min_rr}")

    if reasons:
        return {"direction": "none"}, reasons

    return (
        {
            "direction": direction,
            "entry": round(entry, 2),
            "stop": round(stop, 2),
            "target": round(target, 2),
            "rr": rr,
            "why": str(trade.get("why", "")).strip(),
        },
        [],
    )
