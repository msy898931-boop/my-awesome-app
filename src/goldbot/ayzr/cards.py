"""AYZR's Telegram cards, in Arabic.

Two cards, both deliberately explicit about provenance:

  * :func:`format_decision` shows the read and the trade, and stamps it
    **AYZR / model-written** so a reader never mistakes it for the main bot's
    arithmetic plan. It prints the rejected reasons when a trade was proposed
    and thrown out, because that trail is the point of validating at all.
  * :func:`format_report` shows what the trades did — today and all time — and
    leads with the sample verdict, so a small positive number is never read as
    an edge.

Everything here is pure formatting over plain dicts; no I/O, no state.
"""

from __future__ import annotations

from ..telegram.format import ADVISORY, _esc, _num
from .decide import Decision
from .read import Read
from .report import MIN_SAMPLE, Report, Stats, verdict

_DIR = {"buy": "شراء 🟢", "sell": "بيع 🔴"}


def format_decision(read: Read, decision: Decision) -> str:
    """The AYZR read card: the picture, the trade, and its provenance."""
    lines = ["🤖 *AYZR — قراءة الذكاء الاصطناعي*"]
    lines.append("_محرّك منفصل: قراره من الموديل، وأرقامه محكّمة حسابياً._")
    lines.append("")

    if not decision.configured:
        lines.append(f"⚠️ *تعذّر التحليل بالذكاء الاصطناعي.*\n_{_esc(decision.error or '')}_")
        if decision.error:
            lines.append("")
        lines.append(_macro_line(read))
        lines += ["", ADVISORY]
        return "\n".join(x for x in lines if x is not None)

    if decision.market_state:
        lines.append(f"📊 *حالة السوق:* {_esc(decision.market_state)}")
    if decision.technical:
        lines.append(f"🧮 *الفني:* {_esc(decision.technical)}")
    if decision.fundamental:
        lines.append(f"🌍 *الاقتصادي/السياسي:* {_esc(decision.fundamental)}")
    if decision.bias:
        lines.append(f"🧭 *الانحياز:* {_esc(_bias_ar(decision.bias))}")

    sc = decision.scenarios if isinstance(decision.scenarios, dict) else {}
    primary = sc.get("primary") if isinstance(sc.get("primary"), dict) else None
    if primary:
        lines.append("")
        lines.append(f"🎯 *السيناريو الأساسي:* {_esc(primary.get('thesis', ''))}")
        if primary.get("trigger"):
            lines.append(f"  • يؤكّده: {_esc(primary.get('trigger'))}")
        if primary.get("invalidation"):
            lines.append(f"  • يُبطله: {_esc(primary.get('invalidation'))}")

    lines.append("")
    if decision.has_trade:
        t = decision.trade
        lines.append(f"💼 *الصفقة:* {_DIR.get(t['direction'], t['direction'])}")
        lines.append(f"  • الدخول: `{_num(t['entry'])}`")
        lines.append(f"  • الوقف: `{_num(t['stop'])}`")
        lines.append(f"  • الهدف: `{_num(t['target'])}`")
        lines.append(f"  • المخاطرة/المكافأة: `1:{_num(t['rr'])}`")
        if t.get("zone"):
            lines.append(f"  • المنطقة: {_esc(t['zone'])}")
        if t.get("why"):
            lines.append(f"  • السبب: {_esc(t['why'])}")
        lines.append("")
        lines.append(
            "_مُتحقّق حسابياً: الوقف بالجهة الصحيحة، الدخول داخل منطقة حقيقية، والحافة فوق الحد._"
        )
        lines.append("_سُجّلت في دفتر AYZR مع سيطرة عشوائية لمقارنة النتيجة لاحقاً._")
    else:
        lines.append("🚫 *لا توجد صفقة الآن* — الصورة لا تدعم صفقة واضحة.")
        if decision.rejected:
            lines.append("")
            lines.append("*لماذا رُفض ما اقترحه الموديل:*")
            for reason in decision.rejected[:4]:
                lines.append(f"  • {_esc(reason)}")

    if decision.reasoning:
        lines.append("")
        lines.append(f"🧠 *الشرح:* {_esc(decision.reasoning)}")
    if decision.confidence:
        lines.append(f"_ثقة الموديل التقديرية: {decision.confidence * 100:.0f}%_")

    lines.append("")
    lines.append(_macro_line(read))
    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_report(report: Report, *, today_label: str = "اليوم") -> str:
    """What the trades did: today, all time, and the sample verdict."""
    lines = ["📒 *AYZR — دفتر الصفقات والنتائج*", ""]
    lines.append(_stats_block(today_label, report.today))
    lines.append("")
    lines.append(_stats_block("الإجمالي", report.total))
    lines.append("")
    lines.append(f"⏳ *صفقات مفتوحة (لم تُحسم بعد):* {report.open_now}")
    lines.append(
        f"🚫 *قرارات (لا صفقة):* {report.no_trade_today} اليوم · {report.no_trade_total} إجمالاً"
    )
    lines.append("")
    lines.append(f"⚖️ *الحكم:* {verdict(report.total)}")
    if report.total.n < MIN_SAMPLE:
        lines.append("")
        lines.append(
            "_القاعدة: لا حكم قبل 100 إشارة محسومة، وبمقارنة مع السيطرة العشوائية — "
            "لأن الذهب يميل، وإشارة بلا سيطرة تبدو أذكى مما هي._"
        )
    lines += ["", ADVISORY]
    return "\n".join(lines)


def _stats_block(label: str, st: Stats) -> str:
    if not st.n:
        return f"*{label}:* لا إشارات محسومة بعد."
    hit = f"{st.hit_rate * 100:.0f}%" if st.hit_rate is not None else "—"
    edge = f"{st.edge:+.2f}R" if st.edge is not None else "—"
    ctrl = f"{st.control_mean_r:+.3f}R" if st.control_n else "—"
    return (
        f"*{label}:* {st.n} إشارة محسومة\n"
        f"  • فوز {st.wins} · خسارة {st.losses} · انتهاء وقت {st.timeouts} · بلا تعبئة {st.no_fill}\n"
        f"  • نسبة النجاح: {hit}\n"
        f"  • مجموع R: {st.total_r:+.2f} · متوسط R: {st.mean_r:+.3f}\n"
        f"  • العشوائي: {ctrl} · الفارق (الحافة): {edge}"
    )


def _macro_line(read: Read) -> str:
    macro = (read.news or {}).get("macro") or {}
    if not macro.get("checked"):
        return "🌍 _الماكرو: تعذّر الفحص هذه الجولة._"
    bias = macro.get("bias")
    eur = macro.get("fx_change_1d_pct")
    real = macro.get("real_yield_10y")
    parts = []
    if bias:
        parts.append(str(bias))
    if isinstance(eur, (int, float)):
        parts.append(f"EURUSD {eur:+.2f}%")
    if isinstance(real, (int, float)):
        parts.append(f"عائد حقيقي {real:.2f}%")
    return "🌍 _الماكرو: " + (" · ".join(parts) if parts else "غير متاح") + "_"


def _bias_ar(bias: str) -> str:
    return {"bullish": "صاعد", "bearish": "هابط", "neutral": "محايد"}.get(bias, bias)


__all__ = ["format_decision", "format_report"]
