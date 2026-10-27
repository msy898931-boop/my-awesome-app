"""Message formatting — Arabic cards a trader can read at a glance.

Every card follows the same shape: what the market is doing, what the setup is,
the numbers, and why. The numbers always come from the deterministic layer; the
prose may come from the AI layer. A card never claims certainty, and always
carries the advisory note.
"""

from __future__ import annotations

from typing import Optional

ADVISORY = "⚠️ _تحليل استشاري فقط — ليس نصيحة مالية، والبوت لا ينفّذ أي صفقة._"

BIAS_AR = {"bullish": "صاعد 📈", "bearish": "هابط 📉", "neutral": "محايد ↔️"}
DIR_AR = {"buy": "شراء 🟢", "sell": "بيع 🔴", "none": "لا صفقة ⚪"}


def _esc(text: str) -> str:
    """Escape Markdown control characters so a stray `_` cannot break the card."""
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_plan(plan: dict, analysis: Optional[dict] = None, news: Optional[dict] = None) -> str:
    """The main card: a plan, its numbers, and the reasoning behind it."""
    direction = plan.get("direction", "none")
    if direction == "none":
        return format_status(plan, analysis, news)

    lines = [f"🥇 *XAUUSD — {DIR_AR.get(direction, direction)}*", ""]
    if plan.get("setup"):
        lines.append(f"*النموذج:* {_esc(plan['setup'])}")
    lines.append(f"*الدخول:* `{plan['entry']}`")
    lines.append(f"*الوقف:* `{plan['stop']}`")
    lines.append(f"*الهدف:* `{plan['target']}`")
    lines.append(f"*المخاطرة/المكافأة:* `1:{plan['rr']}`")

    if analysis and analysis.get("confidence"):
        lines.append(f"*ثقة التقدير:* `{round(analysis['confidence'] * 100)}%`")
    if analysis and analysis.get("bias"):
        lines.append(f"*انحياز الفريمات:* {BIAS_AR.get(analysis['bias'], analysis['bias'])}")

    if plan.get("factors"):
        lines += ["", "*الأسباب:*"]
        lines += [f"• {_esc(str(f))}" for f in plan["factors"]]

    reason = plan.get("reason") or (analysis or {}).get("reasoning")
    if reason:
        lines += ["", "*الشرح:*", _esc(reason)]

    if analysis and analysis.get("market_state"):
        lines += ["", "*حالة السوق:*", _esc(analysis["market_state"])]

    if news and (news.get("blackout") or {}).get("active"):
        lines += ["", "🚨 *نافذة خبر عالي التأثير* — يُفضّل تجنّب الدخول الجديد الآن"]

    if plan.get("rejected"):
        lines += ["", "*ما لم يتحقق:*"]
        lines += [f"• {_esc(str(r))}" for r in plan["rejected"][:3]]

    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_status(plan: dict, analysis: Optional[dict] = None, news: Optional[dict] = None) -> str:
    """The no-trade card: why there is nothing to do, plus the market state."""
    lines = ["🥇 *XAUUSD — لا توجد صفقة الآن*", ""]
    if analysis and analysis.get("market_state"):
        lines += ["*حالة السوق:*", _esc(analysis["market_state"]), ""]
    if analysis and analysis.get("bias"):
        lines.append(f"*الانحياز العام:* {BIAS_AR.get(analysis['bias'], analysis['bias'])}")
    if plan.get("rejected"):
        lines += ["", "*الأسباب:*"]
        lines += [f"• {_esc(str(r))}" for r in plan["rejected"][:5]]
    if news:
        blackout = news.get("blackout") or {}
        if blackout.get("active"):
            lines += ["", "🚨 *نافذة خبر عالي التأثير*"]
        upcoming = news.get("upcoming") or []
        if upcoming:
            nxt = upcoming[0]
            lines += ["", f"📅 أقرب حدث: {_esc(str(nxt.get('title')))} "
                          f"[{_esc(str(nxt.get('country')))}] بعد {nxt.get('in_minutes')} دقيقة"]
    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_analysis(sheet: dict, news: Optional[dict] = None) -> str:
    """The `/تحليل` card: the raw SMC read, frame by frame."""
    lines = [f"🥇 *XAUUSD — قراءة SMC/ICT*", ""]
    confluence = sheet.get("confluence") or {}
    lines.append(
        f"*الانحياز:* {BIAS_AR.get(confluence.get('bias', 'neutral'), 'محايد')} "
        f"({confluence.get('bullish_frames', 0)} صاعد / {confluence.get('bearish_frames', 0)} هابط "
        f"من {confluence.get('frames_read', 0)})"
    )
    lines.append("")
    for tf, f in (sheet.get("timeframes") or {}).items():
        event = f.get("last_event") or {}
        pd_zone = (f.get("premium_discount") or {}).get("zone", "—")
        lines.append(
            f"*{tf}* — اتجاه {BIAS_AR.get(f.get('trend'), f.get('trend'))}\n"
            f"  آخر حدث: {event.get('type', '—')} {event.get('direction', '')}\n"
            f"  المنطقة: {pd_zone} | ATR: {f.get('atr')} | إغلاق: {f.get('last_close')}"
        )
    if news:
        blackout = news.get("blackout") or {}
        lines += ["", f"📰 الأخبار: {_esc(str(blackout.get('reason', '—')))}"]
    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_news(news: dict) -> str:
    """The `/أخبار` card."""
    lines = ["📰 *الأخبار والاقتصاد*", ""]
    blackout = news.get("blackout") or {}
    lines.append(f"*النافذة:* {'🚨 نشطة' if blackout.get('active') else 'هادئة'}")
    upcoming = news.get("upcoming") or []
    if upcoming:
        lines += ["", "*القادم:*"]
        for e in upcoming[:5]:
            lines.append(
                f"• {_esc(str(e.get('title')))} [{_esc(str(e.get('country')))}] "
                f"بعد {e.get('in_minutes')}د (توقع {e.get('forecast')})"
            )
    released = news.get("released") or []
    if released:
        lines += ["", "*صدر مؤخراً:*"]
        for e in released[:3]:
            lines.append(f"• {_esc(str(e.get('title')))}: {_esc(str(e.get('gold') or e.get('read')))}")
    headlines = news.get("headlines") or []
    if headlines:
        lines += ["", "*العناوين:*"]
        for h in headlines[:4]:
            lines.append(f"• {_esc(str(h.get('title')))}")
    lines += ["", ADVISORY]
    return "\n".join(lines)
