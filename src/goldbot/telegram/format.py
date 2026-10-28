"""Message formatting — Arabic cards a trader can read at a glance.

Every card follows the same shape: what the market is doing, what the setup is,
the numbers, and why. The numbers always come from the deterministic layer; the
prose may come from the AI layer. A card never claims certainty, and always
carries the advisory note.
"""

from __future__ import annotations

from typing import Optional

ADVISORY = "⚠️ _تحليل استشاري فقط — ليس نصيحة مالية، والبوت لا ينفّذ أي صفقة._"

# Stated on every news card so the mechanical lean is never mistaken for a
# measured edge. It has not been backtested; saying so is cheaper than a
# trader assuming otherwise.
NEWS_METHOD = ("_قراءة ميكانيكية: الفعلي مقابل التقدير، والأثر على الذهب مباشر "
               "مع الدولار فقط. أثر الأخبار على نتائج النظام ما انقاس بعد — لا يُدَّعى حافة._")

BIAS_AR = {"bullish": "صاعد 📈", "bearish": "هابط 📉", "neutral": "محايد ↔️"}
DIR_AR = {"buy": "شراء 🟢", "sell": "بيع 🔴", "none": "لا صفقة ⚪"}


def _esc(text: str) -> str:
    """Escape Markdown control characters so a stray `_` cannot break the card."""
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_price(spot: Optional[dict], plan: Optional[dict] = None,
                 news: Optional[dict] = None) -> str:
    """The automatic push: live price, direction, and the next release.

    Deliberately small, and deliberately free. Everything here is deterministic,
    so an automatic message costs no model tokens at all — the AI prose is only
    produced when the trader asks for the full card. The price is the one thing
    worth pushing unprompted, because it changes on every read while the plan
    behind it rarely does.
    """
    lines = ["🥇 *XAUUSD*"]
    price = (spot or {}).get("close")
    if price is not None:
        lines.append(f"*السعر:* `{price}`")
    else:
        lines.append("*السعر:* غير متاح")

    direction = (plan or {}).get("direction", "none")
    if direction in {"buy", "sell"}:
        lines.append(f"*الاتجاه:* {DIR_AR.get(direction, direction)}")
        if plan.get("setup"):
            lines.append(f"*النموذج:* {_esc(str(plan['setup']))}")

    upcoming = ((news or {}).get("upcoming") or [])
    if (news or {}).get("blackout", {}).get("active"):
        lines.append("🚨 *نافذة خبر عالي التأثير الآن*")
    elif upcoming:
        nxt = upcoming[0]
        lines.append(f"📅 {_esc(str(nxt.get('title')))} بعد {_when(nxt.get('in_minutes'))}")

    lines += ["", ADVISORY]
    return "\n".join(lines)


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


def _when(minutes) -> str:
    """Minutes until (or since) an event, as whole minutes.

    Whole minutes, never ``51.0`` — the float used to leak straight into the
    card. Far-off events stay in minutes rather than rolling up into hours so
    the column reads consistently down the list.
    """
    try:
        m = int(round(float(minutes)))
    except (TypeError, ValueError):
        return "—"
    return "الآن" if m < 0 else f"{m} د"


def _num(value) -> str:
    """A number as written, or an em dash. ``None`` must never reach the card."""
    if value is None or value == "":
        return "—"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return _esc(str(value))


def _gold_lean(event: dict) -> str:
    """The one-line mechanical read, with the comparison spelled out.

    The lean itself is computed in the news layer (``gold_read``); this only
    renders it, so the card and the logic cannot drift apart. A release with no
    baseline gets its honest note, never an invented arrow.
    """
    read = event.get("read")
    if read == "inline":
        return "الفعلي مطابق للتقدير → لا إشارة"
    if read == "direct":
        actual, forecast, previous = (event.get("actual"), event.get("forecast"),
                                      event.get("previous"))
        baseline = forecast if event.get("baseline") == "forecast" else previous
        direction = "أعلى" if (actual or 0) > (baseline or 0) else "أدنى"
        # A stronger dollar pressures gold, so "دعم للذهب" implies a weaker one.
        usd = "أقوى" if "ضغط" in str(event.get("gold", "")) else "أضعف"
        return f"الفعلي {direction} → USD {usd} → {_esc(str(event.get('gold')))}"
    note = event.get("note") or "لا قراءة"
    return _esc(str(note))


def format_news(news: dict) -> str:
    """The `/أخبار` card: the window, what is coming, what printed, and the tape.

    Every line is derived from the feed, never asserted. Where a release has no
    baseline there is no arrow, and where the calendar could not be read the
    card says so instead of showing an empty, reassuring schedule.
    """
    lines = ["📰 *الأخبار والذهب — الآن*", ""]
    source = news.get("source") or {}
    calendar_ok = source.get("calendar_ok", True)
    stale = source.get("calendar_stale", False)
    blackout = news.get("blackout") or {}

    # Never call the window calm on a failed read. "No events" and "no feed"
    # look identical in the data, and only the card can tell them apart.
    if not calendar_ok:
        reason = _esc(str(source.get("calendar_error") or "مصدر التقويم غير متاح"))
        age = " (بيانات قديمة)" if stale else ""
        lines.append(f"⚠️ *تعذّر فحص التقويم*{age}")
        lines.append(f"_المصدر لم يستجب: {reason} — لا يمكن الجزم بوجود خبر_")
    elif blackout.get("active"):
        lines.append("🚨 *نافذة خبر عالي التأثير الآن*")
        for e in (blackout.get("imminent") or [])[:3]:
            lines.append(f"  • {_esc(str(e.get('title')))} "
                         f"[{_esc(str(e.get('country')))}] {_when(e.get('minutes'))}")
    else:
        lines.append("✅ *السوق خالٍ من الأخبار المهمة*")

    upcoming = news.get("upcoming") or []
    if upcoming:
        lines += ["", "⏳ *أحداث قادمة عالية التأثير:*"]
        for e in upcoming[:5]:
            lines.append(
                f"  • {_esc(str(e.get('title')))} [{_esc(str(e.get('country')))}] "
                f"بعد {_when(e.get('in_minutes'))}"
            )
    elif calendar_ok:
        lines += ["", "_لا يوجد حدث عالي التأثير في الأفق._"]

    released = news.get("released") or []
    if released:
        lines += ["", "💱 *صدر للتو:*"]
        for e in released[:5]:
            lines.append(f"  • {_esc(str(e.get('title')))} [{_esc(str(e.get('country')))}] "
                         f"قبل {_when(abs(e.get('in_minutes') or 0))}")
            lines.append(f"    الفعلي {_num(e.get('actual'))} · التقدير {_num(e.get('forecast'))}"
                         f" · السابق {_num(e.get('previous'))}")
            lines.append(f"    {_gold_lean(e)}")

    headlines = news.get("headlines") or []
    if headlines:
        lines += ["", "🗞 *عناوين الذهب:*"]
        for h in headlines[:5]:
            src = _esc(str(h.get("source") or ""))
            suffix = f" — {src}" if src else ""
            lines.append(f"  • {_esc(str(h.get('title')))}{suffix}")

    lines += ["", NEWS_METHOD, "", ADVISORY]
    return "\n".join(lines)
