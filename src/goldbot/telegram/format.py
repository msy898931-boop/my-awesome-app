"""Message formatting — Arabic cards a trader can read at a glance.

Every card follows the same shape: what the market is doing, what the setup is,
the numbers, and why. The numbers always come from the deterministic layer; the
prose may come from the AI layer. A card never claims certainty, and always
carries the advisory note.
"""

from __future__ import annotations

import time

ADVISORY = "⚠️ _تحليل استشاري فقط — ليس نصيحة مالية، والبوت لا ينفّذ أي صفقة._"

# Stated on every news card so the mechanical lean is never mistaken for a
# measured edge. It has not been backtested; saying so is cheaper than a
# trader assuming otherwise.
NEWS_METHOD = (
    "_قراءة ميكانيكية: الفعلي مقابل التقدير، والأثر على الذهب مباشر "
    "مع الدولار فقط. أثر الأخبار على نتائج النظام ما انقاس بعد — لا يُدَّعى حافة._"
)

BIAS_AR = {"bullish": "صاعد 📈", "bearish": "هابط 📉", "neutral": "محايد ↔️"}
DIR_AR = {"buy": "شراء 🟢", "sell": "بيع 🔴", "none": "لا صفقة ⚪"}

# The macro backdrop, in the one word that matters for gold. ``unknown`` is a
# stated non-answer: the macro is quiet, which is not the same as neutral.
MACRO_AR = {
    "tailwind": "ريح داعمة 🌬️",
    "headwind": "ريح معاكسة 🌪️",
    "mixed": "إشارات متضاربة ↔️",
    "neutral": "هادئ ↔️",
    "unknown": "غير محدّد —",
}


def _esc(text: str) -> str:
    """Escape Markdown control characters so a stray `_` cannot break the card."""
    for ch in ("_", "*", "[", "]", "`"):
        text = text.replace(ch, f"\\{ch}")
    return text


def format_price(
    spot: dict | None,
    plan: dict | None = None,
    news: dict | None = None,
    withheld: bool = False,
    analysis: dict | None = None,
) -> str:
    """The automatic push: live price, the trade, the read, and the next release.

    Everything here is deterministic except the optional ``analysis`` prose, so a
    push costs no model tokens of its own — the AI is asked only when an idea is
    actually due, and its reply is handed in. The price is the one thing worth
    pushing unprompted, because it changes on every read while the plan behind it
    rarely does.

    When a plan is present the *numbers* go out with it: a direction without an
    entry, a stop and a target is not a trade a trader can act on, and pushing
    one was the difference between a bot that looks busy and a bot that is
    useful. ``withheld`` is passed by the caller because the caller is what
    stripped the plan: this function cannot tell "an idea was withheld" from
    "there was no idea", and saying the first when the second is true would be
    its own small lie.
    """
    lines = ["🥇 *XAUUSD*"]
    price = (spot or {}).get("close")
    if price is not None:
        lines.append(f"*السعر:* `{price}`")
    else:
        lines.append("*السعر:* غير متاح")

    idea = plan or {}
    direction = idea.get("direction", "none")
    if direction in {"buy", "sell"}:
        lines.append(f"*الاتجاه:* {DIR_AR.get(direction, direction)}")
        if idea.get("setup"):
            lines.append(f"*النموذج:* {_esc(str(idea['setup']))}")
        _trade_block(lines, idea)
        if analysis:
            _analysis_block(lines, analysis)

    wire = news or {}
    upcoming = wire.get("upcoming") or []
    if calendar_unchecked(news):
        # Not "a window is open" — that is a different claim. The read failed, so
        # the window is unknown. The idea is still shown (an unread calendar no
        # longer vetoes), so the warning says the news check is missing from the
        # picture rather than pretending the window is clear. ``withheld`` cannot
        # be true here: it needs an *active* window, which is exactly what an
        # unchecked calendar is not.
        if idea.get("direction") in {"buy", "sell"}:
            lines.append("⚠️ *تعذّر فحص التقويم* — الصفقة معروضة بلا تأكيد خلوّها من خبر")
        else:
            lines.append("⚠️ *تعذّر فحص التقويم* — لا يمكن الجزم بوجود خبر")
    elif (wire.get("blackout") or {}).get("active"):
        lines.append("🚨 *نافذة خبر عالي التأثير الآن*")
    elif upcoming:
        nxt = upcoming[0]
        lines.append(f"📅 {_esc(str(nxt.get('title')))} بعد {_when(nxt.get('in_minutes'))}")

    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_live_price(live: dict | None) -> str:
    """The ``/سعر`` card: the price now, from every source, with the spread.

    Cheap on purpose — no bars, no structure, no model — because the question
    it answers ("where is gold this second?") changes faster than a full read
    can be produced. It shows *each* source's number rather than one blended
    figure, so the trader sees the disagreement instead of being handed a
    false precision. A source that did not answer is named as missing: an
    absent quote must never read as agreement.
    """
    lines = ["💰 *XAUUSD — السعر اللحظي*"]
    data = live or {}
    price = data.get("price")
    if price is None:
        # Not a price of zero, and not silence: the check ran and nothing
        # answered. Say so, the same way the news card does.
        lines.append("")
        lines.append("⚠️ *تعذّر جلب السعر* — كل المصادر لم تستجب.")
        lines.append("_جرّب مرة أخرى بعد لحظات._")
        lines += ["", ADVISORY]
        return "\n".join(lines)

    lines.append(f"*السعر:* `{price}`")
    sources = data.get("sources") or 0
    spread = data.get("spread_pct")
    if spread is not None:
        spread_word = "متطابقة" if spread < 0.05 else ("متقاربة" if spread < 0.3 else "متباعدة")
        lines.append(f"*توافق المصادر:* {sources} مصدر — {spread_word} (فارق {spread}%)")
    low, high = data.get("low"), data.get("high")
    if low is not None and high is not None and low != high:
        lines.append(f"*النطاق:* `{low}` — `{high}`")

    quotes = data.get("quotes") or {}
    if quotes:
        lines.append("")
        lines.append("*حسب المصدر:*")
        for name, value in sorted(quotes.items(), key=lambda kv: -kv[1]):
            lines.append(f"• `{name}` — `{value}`")

    stamp = _clock(data.get("time"))
    if stamp:
        lines.append("")
        lines.append(f"🕐 آخر تحديث: `{stamp}` UTC")

    lines += ["", ADVISORY]
    return "\n".join(lines)


def _trade_block(lines: list[str], idea: dict) -> None:
    """The three numbers a trader acts on, when they exist.

    Written only when entry, stop and target are all present: a partial set is
    worse than none, because a stop without a target invites a reader to fill in
    the blank themselves.
    """
    entry, stop, target = idea.get("entry"), idea.get("stop"), idea.get("target")
    if entry is None or stop is None or target is None:
        return
    lines.append(f"*الدخول:* `{_num(entry)}`")
    lines.append(f"*الوقف:* `{_num(stop)}`")
    lines.append(f"*الهدف:* `{_num(target)}`")
    if idea.get("rr"):
        lines.append(f"*المخاطرة/المكافأة:* `1:{idea['rr']}`")


def _analysis_block(lines: list[str], analysis: dict) -> None:
    """The model's prose, and only when it is actually the model's.

    A fallback read has no confidence and must not be dressed as a probability,
    so the confidence line is emitted only when it is a real number. The
    reasoning is the point of asking: it is what turns a set of levels into a
    decision a trader can weigh.
    """
    reasoning = str(analysis.get("reasoning") or "").strip()
    confidence = analysis.get("confidence")
    if not reasoning and not confidence:
        return
    if confidence:
        lines.append(f"*ثقة التقدير:* `{round(float(confidence) * 100)}%`")
    if reasoning:
        lines += ["", "*الشرح:*", _esc(reasoning)]
    model = str(analysis.get("model") or "").strip()
    if model:
        # Provenance: a read without its author is an opinion with no source.
        lines.append(f"_المصدر: {_esc(model)}_")


def calendar_unchecked(news: dict | None) -> bool:
    """Whether the calendar could not be read, so the window is unknown.

    ``digest`` sets ``checked=False`` when the fetch failed and forces
    ``active=False``, because an unread window is not an *open* one. An unknown
    window is not a *clear* one either, so a card must not stay silent about it
    — silence reads as calm. This predicate lets a card say which of the three
    facts it has: a window is open, no window was found, or the window could not
    be read. Only the first withholds an idea; the third is a warning.
    """
    blackout = (news or {}).get("blackout") or {}
    return blackout.get("checked") is False and not blackout.get("active")


def blackout_blocks_idea(news: dict | None) -> bool:
    """Whether the stated rule withholds a *new* idea right now.

    One predicate for both paths — the automatic push and the card a trader asks
    for — so the rule cannot be honoured on one and bypassed by a button on the
    other.

    Only a **known open window** withholds: a high-impact release is printing now
    or is imminent, which is a stated, checkable rule. An *unread* calendar does
    not veto. A rate-limited feed would otherwise withhold every idea for as long
    as the source stays blocked — and since the feed blocks by IP, that turned a
    transient 429 into a bot that showed nothing at all. The safe answer to "we
    could not check" is to show the idea and say the check failed, not to hide a
    plan the trader cannot otherwise see. The card carries the warning; the
    trader decides.
    """
    blackout = (news or {}).get("blackout") or {}
    return bool(blackout.get("active"))


def macro_lines(news: dict | None, direction: str = "none") -> list[str]:
    """The macro backdrop as card lines, or ``[]`` when there is nothing to say.

    This is context, not a signal: it never changes the direction a plan chose.
    It exists because the study found the setup is beta to gold, so a trader is
    owed the name of the wind the plan is standing in — most of all when the
    plan is leaning into it. A quiet or unread macro adds nothing rather than a
    reassuring default, so an unchecked feed prints a plain "could not read"
    instead of silence that reads as calm.
    """
    macro = (news or {}).get("macro") or {}
    if not macro:
        return []
    if not macro.get("checked"):
        return ["", "🌍 *الماكرو:* تعذّر الفحص — لا يُدَّعى شيء عن الدولار."]
    bias = str(macro.get("bias") or "unknown")
    label = MACRO_AR.get(bias, bias)
    bits: list[str] = []
    fx = macro.get("fx_change_1d_pct")
    if fx is not None:
        arrow = "▲" if fx > 0 else "▼" if fx < 0 else "="
        bits.append(f"EURUSD {arrow}{abs(fx):.2f}%")
    ry = macro.get("real_yield_10y")
    if ry is not None:
        bits.append(f"عائد حقيقي {ry:.2f}%")
    tail = f" ({' · '.join(bits)})" if bits else ""
    lines = ["", f"🌍 *الماكرو:* {label}{tail}"]
    if bias == "headwind" and direction == "buy":
        lines.append("_⚠️ الشراء يسبح عكس اتجاه الدولار — هذا بيتا على الذهب، لا حافة._")
    elif bias == "tailwind" and direction == "sell":
        lines.append("_⚠️ البيع يسبح عكس اتجاه الدولار — هذا بيتا على الذهب، لا حافة._")
    return lines


def format_plan(plan: dict, analysis: dict | None = None, news: dict | None = None) -> str:
    """The main card: a plan, its numbers, and the reasoning behind it.

    During a high-impact blackout the card degrades to the no-trade read. The
    *idea* — direction, entry, stop, target, confidence — is what a blackout
    exists to suppress, so none of it is shown; the numbers a trader needs to
    act on are exactly the numbers withheld. ``format_status`` says why, so a
    missing block is never mistaken for a market verdict.

    An *unread* calendar is not a blackout and does not degrade the card: the
    trade is shown with a warning that the news check failed. Withholding on an
    unread feed turned a transient 429 into a bot that showed nothing.
    """
    direction = plan.get("direction", "none")
    if direction == "none" or blackout_blocks_idea(news):
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
    model = str((analysis or {}).get("model") or "").strip()
    if model:
        # Provenance: a read without its author is an opinion with no source.
        lines.append(f"_المصدر: {_esc(model)}_")

    if analysis and analysis.get("market_state"):
        lines += ["", "*حالة السوق:*", _esc(analysis["market_state"])]

    if plan.get("rejected"):
        lines += ["", "*ما لم يتحقق:*"]
        lines += [f"• {_esc(str(r))}" for r in plan["rejected"][:3]]

    lines += macro_lines(news, direction)
    if calendar_unchecked(news):
        # The trade is shown even when the news check failed — the check is a
        # missing input, not a veto. Saying so is what keeps "shown anyway" from
        # reading as "confirmed clear of news".
        lines += [
            "",
            "⚠️ *تعذّر فحص التقويم* — الصفقة معروضة بلا تأكيد خلوّها من خبر عالي التأثير.",
        ]
    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_status(plan: dict, analysis: dict | None = None, news: dict | None = None) -> str:
    """The no-trade card: why there is nothing to do, plus the market state.

    Two different reasons reach this card and they must not look alike: the
    market offered no setup, or it offered one that the blackout withholds. The
    second is said out loud — otherwise a withheld idea reads as a flat market,
    which is a different claim about the world. An unread calendar is a third
    thing again: it is reported as a failed check, never as a withheld idea,
    because an unread window is not an open one.
    """
    withheld = blackout_blocks_idea(news) and plan.get("direction") in {"buy", "sell"}
    unchecked = calendar_unchecked(news)
    lines = ["🥇 *XAUUSD — لا توجد صفقة الآن*", ""]
    if withheld:
        # Only a *known* open window reaches here now; an unread calendar no
        # longer withholds. So the reason is always the news window.
        lines += [
            "🚨 *يوجد نموذج لكنه محجوب* — نافذة خبر عالي التأثير.",
            "_تُعرض الأرقام بعد انتهاء النافذة._",
            "",
        ]
    if analysis and analysis.get("market_state"):
        lines += ["*حالة السوق:*", _esc(analysis["market_state"]), ""]
    if analysis and analysis.get("bias"):
        lines.append(f"*الانحياز العام:* {BIAS_AR.get(analysis['bias'], analysis['bias'])}")
    if analysis and not withheld:
        # The reasoning and its author belong on the no-trade card too: "why
        # nothing" is the question this card exists to answer. Withheld means a
        # window is open, and the analysis would leak the idea it suppresses.
        _analysis_block(lines, analysis)
    if plan.get("rejected"):
        lines += ["", "*الأسباب:*"]
        lines += [f"• {_esc(str(r))}" for r in plan["rejected"][:5]]
    if news:
        blackout = news.get("blackout") or {}
        if unchecked and not withheld:
            # The window is unknown and nothing was withheld — still not calm.
            lines += ["", "⚠️ *تعذّر فحص التقويم* — لا يمكن الجزم بوجود خبر"]
        elif blackout.get("active") and not withheld:
            lines += ["", "🚨 *نافذة خبر عالي التأثير*"]
        upcoming = news.get("upcoming") or []
        if upcoming:
            nxt = upcoming[0]
            lines += [
                "",
                f"📅 أقرب حدث: {_esc(str(nxt.get('title')))} "
                f"[{_esc(str(nxt.get('country')))}] بعد {nxt.get('in_minutes')} دقيقة",
            ]
    lines += macro_lines(news, plan.get("direction", "none"))
    lines += ["", ADVISORY]
    return "\n".join(lines)


def format_alert(event: dict, news: dict | None = None) -> str:
    """The unprompted heads-up: a high-impact release is about to print.

    Sent *before* the window opens, because once the blackout is active the idea
    is already withheld and the warning is too late to be worth anything. It
    carries the clock time as well as the countdown: "in 22 minutes" is useless
    if the reader opens it an hour later, and a wall-clock time is what a trader
    can put in a diary.
    """
    title = _esc(str(event.get("title") or "خبر"))
    country = _esc(str(event.get("country") or "—"))
    lines = [
        f"🔔 *خبر عالي التأثير بعد {_when(event.get('in_minutes'))}*",
        "",
        f"*{title}* [{country}]",
    ]
    when = _clock(event.get("time"))
    if when:
        lines.append(f"🕐 الموعد: `{when}` UTC")
    forecast, previous = _num(event.get("forecast")), _num(event.get("previous"))
    if forecast != "—" or previous != "—":
        lines.append(f"*التقدير:* {forecast} · *السابق:* {previous}")
    lines += [
        "",
        "_تُحجب الأفكار الجديدة داخل النافذة (15 د قبل و15 د بعد). الأرقام تعود بعد انتهائها._",
    ]
    blackout = (news or {}).get("blackout") or {}
    if blackout.get("active"):
        lines.append("🚨 _النافذة مفتوحة الآن._")
    lines += ["", ADVISORY]
    return "\n".join(lines)


def _clock(epoch) -> str:
    """A unix time as ``HH:MM`` UTC, or '' when it is missing or malformed."""
    try:
        stamp = int(epoch)
    except (TypeError, ValueError):
        return ""
    return time.strftime("%H:%M", time.gmtime(stamp))


def format_analysis(sheet: dict, news: dict | None = None) -> str:
    """The `/تحليل` card: the raw SMC read, frame by frame."""
    lines = ["🥇 *XAUUSD — قراءة SMC/ICT*", ""]
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
        actual, forecast, previous = (
            event.get("actual"),
            event.get("forecast"),
            event.get("previous"),
        )
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
    usable = source.get("calendar_usable", calendar_ok)
    blackout = news.get("blackout") or {}

    # Three facts, and the card must not blur them: a fresh read, a saved read
    # standing in for a blocked one, or nothing at all. "No events" and "no
    # feed" look identical in the data, and only the card can tell them apart.
    if not usable:
        reason = _esc(str(source.get("calendar_error") or "مصدر التقويم غير متاح"))
        lines.append("⚠️ *تعذّر فحص التقويم*")
        lines.append(f"_المصدر لم يستجب: {reason} — لا يمكن الجزم بوجود خبر_")
    elif not calendar_ok:
        # A saved read is in use: the window is known, but it is not this
        # minute's. Say both, so "stale" is not mistaken for "current".
        reason = _esc(str(source.get("calendar_error") or "المصدر لم يستجب"))
        lines.append("🕗 *التقويم من قراءة محفوظة (بيانات قديمة)*")
        lines.append(f"_تعذّر التحديث الآن: {reason} — تُعرض آخر قراءة ناجحة_")
        if blackout.get("active"):
            lines.append("🚨 *نافذة خبر عالي التأثير الآن*")
            for e in (blackout.get("imminent") or [])[:3]:
                lines.append(
                    f"  • {_esc(str(e.get('title')))} "
                    f"[{_esc(str(e.get('country')))}] {_when(e.get('minutes'))}"
                )
    elif blackout.get("active"):
        lines.append("🚨 *نافذة خبر عالي التأثير الآن*")
        for e in (blackout.get("imminent") or [])[:3]:
            lines.append(
                f"  • {_esc(str(e.get('title')))} "
                f"[{_esc(str(e.get('country')))}] {_when(e.get('minutes'))}"
            )
    else:
        lines.append("✅ *السوق خالٍ من الأخبار المهمة*")

    upcoming = news.get("upcoming") or []
    if upcoming:
        lines += ["", "⏳ *أحداث قادمة عالية التأثير:*"]
        for e in upcoming[:5]:
            clock = _clock(e.get("time"))
            stamp = f" 🕐 `{clock}`" if clock else ""
            lines.append(
                f"  • {_esc(str(e.get('title')))} [{_esc(str(e.get('country')))}] "
                f"بعد {_when(e.get('in_minutes'))}{stamp}"
            )
    elif calendar_ok:
        lines += ["", "_لا يوجد حدث عالي التأثير في الأفق._"]

    released = news.get("released") or []
    if released:
        lines += ["", "💱 *صدر للتو:*"]
        for e in released[:5]:
            lines.append(
                f"  • {_esc(str(e.get('title')))} [{_esc(str(e.get('country')))}] "
                f"قبل {_when(abs(e.get('in_minutes') or 0))}"
            )
            lines.append(
                f"    الفعلي {_num(e.get('actual'))} · التقدير {_num(e.get('forecast'))}"
                f" · السابق {_num(e.get('previous'))}"
            )
            lines.append(f"    {_gold_lean(e)}")

    headlines = news.get("headlines") or []
    if headlines:
        lines += ["", "🗞 *عناوين الذهب:*"]
        for h in headlines[:5]:
            src = _esc(str(h.get("source") or ""))
            suffix = f" — {src}" if src else ""
            lines.append(f"  • {_esc(str(h.get('title')))}{suffix}")

    lines += _macro_section(news)
    # No headlines and the feeds were down: say so, rather than let an empty
    # section read as "nothing happened in the world". With stale headlines
    # shown, the reader still gets the news, but the age is flagged.
    if not source.get("rss_ok", True):
        age = " (بيانات قديمة)" if source.get("rss_stale") else ""
        reason = _esc(str(source.get("rss_error") or "مصدر العناوين غير متاح"))
        if headlines:
            lines.append(f"_⚠️ العناوين قد تكون قديمة{age} — المصدر لم يستجب: {reason}_")
        else:
            lines += ["", f"⚠️ *تعذّر جلب العناوين*{age}", f"_المصدر لم يستجب: {reason}_"]

    lines += ["", NEWS_METHOD, "", ADVISORY]
    return "\n".join(lines)


def _macro_section(news: dict) -> list[str]:
    """The macro block on the news card: the numbers, and what they mean.

    Shown with its own honesty note, because the correlation is real but it is
    contemporaneous — the numbers describe the wind now, not where gold goes
    next. A failed read says so rather than printing a calm blank.
    """
    macro = news.get("macro") or {}
    if not macro:
        return []
    lines = ["", "🌍 *الماكرو (سياق لا إشارة):*"]
    if not macro.get("checked"):
        reason = _esc(str(macro.get("error") or "المصدر لم يستجب"))
        lines.append(f"_⚠️ تعذّر الفحص: {reason}_")
        return lines

    fx = macro.get("fx_change_1d_pct")
    if fx is not None:
        arrow = "▲" if fx > 0 else "▼" if fx < 0 else "="
        lines.append(f"  • EURUSD: {arrow}{abs(fx):.2f}% خلال اليوم")
    ry, ryc = macro.get("real_yield_10y"), macro.get("real_yield_change")
    if ry is not None:
        move = f" ({'+' if (ryc or 0) >= 0 else ''}{ryc:.2f} خلال 5 جلسات)" if ryc else ""
        lines.append(f"  • العائد الحقيقي 10 سنوات: {ry:.2f}%{move}")
    dollar, dc = macro.get("broad_dollar"), macro.get("broad_dollar_change")
    if dollar is not None:
        move = f" ({'+' if (dc or 0) >= 0 else ''}{dc:.2f})" if dc else ""
        lines.append(f"  • مؤشر الدولار الواسع: {dollar:.1f}{move}")

    bias = str(macro.get("bias") or "unknown")
    lines.append(f"  • *الخلاصة:* {MACRO_AR.get(bias, bias)}")
    if bias in {"tailwind", "headwind"}:
        lines.append(
            "_ارتباط الذهب بالدولار نحو -0.6، لكنه متزامن لا سابق — يوصف الريح ولا يتنبّأ بها._"
        )
    return lines
