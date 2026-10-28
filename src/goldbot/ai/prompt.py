"""The SMC/ICT analyst persona and the prompt that carries the facts.

The system prompt fixes the persona and, more importantly, the honesty rules:
the model may only reason over facts it was handed, must say "no trade" when
the facts do not support one, and must express confidence as an estimate. The
user prompt is the fact sheet plus the news digest, serialised compactly so it
fits a free-tier token ceiling.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """أنت محلّل تداول محترف متخصّص في الذهب (XAUUSD)، وتعمل حصراً بمدرسة \
Smart Money Concepts (SMC) و Inner Circle Trader (ICT).

قواعد صارمة لا تُخالَف:
1. كل الأرقام (المستويات، الوقف، الأهداف، ATR) محسوبة مسبقاً وموجودة في ورقة الحقائق. \
لا تخترع أي مستوى أو رقم من عندك. إن احتجت رقماً فاستخدم الموجود فقط.
2. إذا لم تدعم الحقائق صفقةً واضحة، قل "لا توجد صفقة الآن" واشرح السبب. \
الصمت المهني أفضل من صفقة ضعيفة.
3. اعتمد على مفاهيم المدرسة: كسر البنية (BOS)، تغيّر الشخصية (CHoCH)، \
الفجوات السعرية (FVG)، كتل الأوامر (OB)، السيولة (EQH/EQL)، \
الكنس (Sweeps)، منطقة Premium/Discount، وجلسات السوق.
4. لا تستخدم أبداً عبارات الجزم مثل "سيرتفع حتماً" أو "سيهبط". \
استخدم لغة الاحتمال: "يُرجّح"، "السيناريو الأقرب"، "ضغط".
5. اشرح بالأرقام المذكورة في الحقائق: اذكر المستوى ورقمه.
6. أجب بالعربية الفصحى المبسّطة، وبتنظيم واضح.

مخرجاتك يجب أن تكون JSON صالحاً فقط بهذا الشكل:
{
  "market_state": "شرح حالة السوق الحالية بناءً على المدرسة (2-4 جمل)",
  "bias": "bullish|bearish|neutral",
  "setup": "اسم النموذج المكتشف مثل: CHoCH + FVG retest، أو null إن لم يوجد",
  "reasoning": "شرح مفصّل ليش هذا القرار، خطوة بخطوة، مع ذكر الأرقام (3-6 جمل)",
  "confidence": 0.0,
  "trade": {
     "direction": "buy|sell|none",
     "entry": 0.0, "stop": 0.0, "target": 0.0,
     "rr": 0.0,
     "why": "السبب المحدد لهذه الصفقة بالأرقام"
  },
  "warnings": ["تحذير1", "تحذير2"]
}

قيمة confidence رقم بين 0 و 1 يمثّل تقديرك لاحتمال نجاح السيناريو. \
اجعلها متحفّظة: 0.6 تعني سيناريو جيد وليس مؤكداً."""


def build_prompt(facts: dict, news: dict | None = None, compact: bool = True) -> str:
    """Serialise the fact sheet (and news) into the user message.

    ``compact`` trims the sheet to the fields the model actually reasons over,
    which keeps the request inside a free-tier token ceiling. The levels it may
    cite survive the trim; the bulk that only bloats the prompt does not.
    """
    payload = {
        "symbol": facts.get("symbol"),
        "confluence": facts.get("confluence"),
        "timeframes": _trim_frames(facts.get("timeframes", {}))
        if compact
        else facts.get("timeframes"),
    }
    if news:
        payload["news"] = {
            "blackout": news.get("blackout"),
            "upcoming": news.get("upcoming", [])[:4],
            "released": news.get("released", [])[:3],
            "headlines": [h.get("title") for h in news.get("headlines", [])[:4]],
        }
        macro = news.get("macro") or {}
        if macro.get("checked"):
            # Context, not a fact to trade: the model is told the wind, and told
            # it does not predict. Left out entirely when unread, so silence
            # never reads as "macro is calm".
            payload["macro"] = {
                "bias": macro.get("bias"),
                "eurusd_change_1d_pct": macro.get("fx_change_1d_pct"),
                "real_yield_10y": macro.get("real_yield_10y"),
                "real_yield_change_5d": macro.get("real_yield_change"),
                "note": "سياق فقط: الارتباط متزامن لا سابق، ولا يغيّر اتجاه الخطة.",
            }
    return (
        "ورقة الحقائق المحسوبة من الشمعات المغلقة:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\nحلّل بناءً على هذه الحقائق فقط، وأخرج JSON صالحاً."
    )


def _trim_frames(frames: dict) -> dict:
    """Keep only what a live read actually cites.

    Arabic is token-dense and the free tier meters 8000 tokens per minute, so
    the fact sheet has to stay lean. The levels the model may quote survive;
    the long tails of past zones do not.
    """
    keep = (
        "trend",
        "last_close",
        "atr",
        "last_event",
        "premium_discount",
        "last_swing_high",
        "last_swing_low",
    )
    out: dict = {}
    for tf, facts in frames.items():
        trimmed = {k: facts.get(k) for k in keep if k in facts}
        # Only the nearest unfilled zones matter for a decision now.
        trimmed["fair_value_gaps"] = _nearest(
            [g for g in (facts.get("fair_value_gaps") or []) if not g.get("filled")],
            facts.get("last_close"),
            2,
        )
        trimmed["order_blocks"] = _nearest(
            [o for o in (facts.get("order_blocks") or []) if not o.get("mitigated")],
            facts.get("last_close"),
            2,
        )
        trimmed["sweeps"] = (facts.get("sweeps") or [])[-2:]
        liq = facts.get("liquidity") or {}
        trimmed["liquidity"] = {
            "equal_highs": (liq.get("equal_highs") or [])[:2],
            "equal_lows": (liq.get("equal_lows") or [])[:2],
        }
        out[tf] = trimmed
    return out


def _nearest(zones: list[dict], close, count: int) -> list[dict]:
    """The ``count`` zones closest to price, which are the tradeable ones."""
    if not zones:
        return []
    if not isinstance(close, (int, float)):
        return zones[:count]
    return sorted(
        zones,
        key=lambda z: abs(((z.get("top", 0) + z.get("bottom", 0)) / 2) - close),
    )[:count]
