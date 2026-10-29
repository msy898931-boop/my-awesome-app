"""AYZR's analyst persona and prompt.

AYZR's whole reason to exist is to let a model **weigh the full picture** and
name a trade, rather than only narrating a plan the arithmetic already fixed.
So the prompt hands it everything at once — structure, the news calendar, the
headlines, and the macro wind — and asks for a decision, not a description.

Two honesty rules are load-bearing and are stated to the model in as many words:

  * every price comes from the fact sheet; the model may **select** a level but
    may never invent one, and
  * when the picture does not support a trade, the model must say so. A traded
    "no" is worth more than an invented signal, because AYZR is journalled and
    a fabricated entry would poison the very record we are keeping to judge it.

AYZR produces more than one scenario on purpose: a directional trade *and* the
invalidation that would flip it. A single call, so the free tier is respected.
"""

from __future__ import annotations

import json

SYSTEM_PROMPT = """أنت AYZR، محلّل تداول محترف متخصّص حصراً في الذهب (XAUUSD)، تجمع بين \
مدرسة Smart Money Concepts (SMC/ICT) وتحليل أساسي (اقتصادي وسياسي) للسياق الكلّي.

مهمّتك: أن تقرأ الصورة الكاملة وتعطي **قراراً تداولياً**، لا وصفاً فقط.

قواعد صارمة لا تُخالَف:
1. كل الأرقام (المستويات، الوقف، الهدف) موجودة مسبقاً في ورقة الحقائق. \
اختر من الموجود فقط، ولا تخترع أي مستوى أو رقم من عندك.
2. يجب أن يكون سعر الدخول داخل منطقة (FVG أو Order Block) من ورقة الحقائق، \
أو عند مستوى بنيوي مذكور فيها (قمة/قاع/سيولة). وإن لم يوجد، قل لا توجد صفقة.
3. الوقف يجب أن يكون على الجهة الصحيحة (تحت الدخول في الشراء، فوقه في البيع) \
وأن يكون هدفك محقّقاً لنسبة مخاطرة/مكافأة لا تقل عن الحد المطلوب.
4. إذا لم تدعم الصورة صفقة واضحة، قل لا توجد صفقة الآن واشرح السبب. \
عدم التداول قرار، وهو أفضل من صفقة مخترعة.
5. وازن ثلاثة طبقات — وكلّ واحدة لها وزنها:
   - **الفني/الرياضي**: البنية (BOS/CHoCH)، الفجوات (FVG)، كتل الأوامر (OB)، \
السيولة (EQH/EQL)، الكنس، Premium/Discount، ATR.
   - **الاقتصادي**: التقويم (نافذة خبر عالي التأثير، الفائدة، التضخّم، الوظائف) \
وعائد السند الحقيقي.
   - **السياسي/الكلي**: العناوين، اتجاه الدولار (EURUSD)، موقف الذهب من الريح.
6. لا تجزم أبداً. استخدم لغة الاحتمال: "يُرجّح"، "السيناريو الأقرب"، "ضغط".
7. اذكر الأرقام: كل مستوى تذكره يجب أن يكون مذكوراً في الحقائق برقمه.
8. أجب بالعربية الفصحى المبسّطة، وبتنظيم واضح.

مخرجاتك JSON صالح فقط بهذا الشكل:
{
  "market_state": "حالة السوق الآن، دمجاً بين البنية والماكرو (2-4 جمل)",
  "bias": "bullish|bearish|neutral",
  "technical": "القراءة الفنية بالأرقام (2-4 جمل)",
  "fundamental": "القراءة الاقتصادية/السياسية بالأرقام (2-3 جمل)",
  "reasoning": "لماذا هذا القرار، خطوة بخطوة، بأرقام (3-6 جمل)",
  "scenarios": {
     "primary": {"thesis": "...", "trigger": "ما يؤكّده بأرقام", "invalidation": "ما يُبطله بأرقام"},
     "alternative": {"thesis": "...", "trigger": "...", "invalidation": "..."}
  },
  "confidence": 0.0,
  "trade": {
     "direction": "buy|sell|none",
     "entry": 0.0, "stop": 0.0, "target": 0.0,
     "zone": "المنطقة أو المستوى الذي اخترته من الحقائق",
     "why": "السبب المحدد بالأرقام"
  },
  "warnings": ["تحذير1", "تحذير2"]
}

confidence رقم بين 0 و 1 يمثّل تقديرك لاحتمال نجاح السيناريو الأساسي. \
كن متحفّظاً: 0.6 تعني سيناريو جيد، لا مؤكد. وإن لم توجد صفقة اجعل direction="none"."""


def build_prompt(facts: dict, news: dict | None = None, min_rr: float = 2.0) -> str:
    """Serialise the full picture — structure, news, macro — into one message.

    The macro block is only included when it was actually read: an unread macro
    leg is left out rather than sent empty, so the model is never handed silence
    that it could mistake for "the wind is calm".
    """
    payload: dict = {
        "symbol": facts.get("symbol"),
        "minimum_rr": min_rr,
        "confluence": facts.get("confluence"),
        "timeframes": _trim(facts.get("timeframes", {})),
    }
    if news:
        payload["news"] = {
            "blackout": news.get("blackout"),
            "upcoming": news.get("upcoming", [])[:5],
            "released": news.get("released", [])[:3],
            "headlines": [h.get("title") for h in news.get("headlines", [])[:5]],
        }
        macro = news.get("macro") or {}
        if macro.get("checked"):
            payload["macro"] = {
                "bias": macro.get("bias"),
                "eurusd_change_1d_pct": macro.get("fx_change_1d_pct"),
                "real_yield_10y": macro.get("real_yield_10y"),
                "real_yield_change_5d": macro.get("real_yield_change"),
                "note": "سياق فقط، لا يقلب الاتجاه وحده.",
            }
    return (
        "ورقة الحقائق المحسوبة من الشمعات المغلقة، ومعها الصورة الإخبارية والماكرو:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        + "\n\nاختر صفقة من هذه الأرقام فقط، أو قل لا توجد صفقة. أخرج JSON صالحاً."
    )


def _trim(frames: dict) -> dict:
    """Keep the levels a live decision can actually cite.

    Arabic is token-dense and the free tier meters by the minute, so the sheet is
    trimmed to the fields a decision uses: trend, last close, ATR, the premium/
    discount zone, the recent swings, the nearest unfilled gaps and order blocks,
    the last sweeps, and the liquidity pools.
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
        trimmed["fair_value_gaps"] = _nearest(
            [g for g in (facts.get("fair_value_gaps") or []) if not g.get("filled")],
            facts.get("last_close"),
            3,
        )
        trimmed["order_blocks"] = _nearest(
            [o for o in (facts.get("order_blocks") or []) if not o.get("mitigated")],
            facts.get("last_close"),
            3,
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
    """The ``count`` zones closest to price — the candidate entries."""
    if not zones:
        return []
    if not isinstance(close, (int, float)):
        return zones[:count]
    return sorted(
        zones,
        key=lambda z: abs(((z.get("top", 0) + z.get("bottom", 0)) / 2) - close),
    )[:count]
