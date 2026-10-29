"""The bot's surface: help text, the button menus, and the command resolver.

Everything here is pure data and pure functions — no I/O, no bot state — so the
keyboards and the payload mapping can be tested on their own. The dispatcher in
``screens.py`` reads these; ``bot.py`` re-exports them so a caller that always
imported from there keeps working.
"""

from __future__ import annotations

HELP = (
    "🥇 *بوت الذهب — SMC/ICT*\n\n"
    "⌨️ *لوحة التحكم أسفل الشاشة* — الأزرار ثابتة هناك، اضغط أي زر في أي وقت:\n"
    "💰 السعر · 📊 التحليل · 🎯 الصفقة · 📈 الحالة · 📰 الأخبار · 🤖 AYZR · "
    "📒 دفتر AYZR · 🔄 تحديث · ⚙️ الإعدادات\n\n"
    "أو اكتب الأمر:\n"
    "/price — السعر اللحظي الآن من كل المصادر\n"
    "/analyse — قراءة SMC/ICT الكاملة لكل فريم\n"
    "/plan — آخر صفقة أو حالة السوق الآن\n"
    "/news — التقويم الاقتصادي والعناوين\n"
    "/status — ملخّص سريع\n"
    "/refresh — قراءة جديدة فوراً (تتجاوز الكاش)\n"
    "/settings — تغيير الفريمات والحساسية\n\n"
    "🤖 *AYZR* — محرّك منفصل يعتمد على الذكاء الاصطناعي في القرار:\n"
    "/ayzr — قراءة AYZR الكاملة (فني + اقتصادي/سياسي) وصفقتها\n"
    "/دفتري — دفتر نتائج AYZR اليوم والإجمالي\n"
    "_AYZR يستعمل حصته الخاصة، ولا يمسّ قرارات البوت الأساسي._\n\n"
    "البوت يفحص السوق كل فترة ويرسل عند ظهور فكرة جديدة.\n"
    "تحليل استشاري فقط — لا ينفّذ أي صفقة."
)

# Every frame the data providers can serve. Order is the display order, widest
# last, so the settings grid reads from fast to slow.
ALL_TIMEFRAMES: tuple[str, ...] = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")

# Sensitivity is the minimum reward-to-risk the plan must clear. A lower bar
# admits more setups (more signals, thinner ones); a higher bar admits fewer,
# cleaner ones. Stored as (callback key, label, value).
SENSITIVITY: tuple[tuple[str, str, float], ...] = (
    ("1.5", "🟢 حساسية عالية", 1.5),
    ("2.0", "🟡 متوازنة", 2.0),
    ("3.0", "🔴 صارمة", 3.0),
)

# The buttons: label -> callback data. Callback data is ASCII and short,
# because Telegram caps it at 64 bytes, and the label carries the Arabic.
# The AYZR row is its own: an AI-first engine that lives beside the bot, on its
# own budget and its own journal, reached only through this namespace.
MENU: list[list[tuple[str, str]]] = [
    [("📊 التحليل الكامل", "analyse"), ("🎯 الصفقة", "plan")],
    [("💰 السعر اللحظي", "price"), ("📈 حالة السوق", "status")],
    [("📰 الأخبار", "news"), ("🔄 تحديث", "refresh")],
    [("🤖 تحليل AYZR", "ayzr:analyse"), ("📒 دفتر AYZR", "ayzr:report")],
    [("⚙️ الإعدادات", "settings")],
]

# The persistent panel pinned to the bottom of the chat. A *reply* keyboard,
# not an inline one: it stays on screen while the trader scrolls, so a button
# is never lost inside an old card. Each button sends its own text, and the
# leading emoji is what ``_respond`` maps back to a command, so this list and
# MENU must keep the same emoji heads.
REPLY_MENU: list[list[str]] = [
    ["💰 السعر", "📊 التحليل"],
    ["🎯 الصفقة", "📈 الحالة"],
    ["📰 الأخبار", "🔄 تحديث"],
    ["🤖 AYZR", "📒 دفتر AYZR"],
    ["⚙️ الإعدادات"],
]

PANEL_HINT = "⌨️ لوحة التحكم أسفل الشاشة — اضغط أي زر في أي وقت."

# Older releases used the leading emoji itself as the callback data. Telegram
# keeps those keyboards on every message already in the chat, so a tap on an
# old card still arrives here as "🔄" and used to dead-end on "أمر غير معروف".
# The map is derived from MENU rather than hand-written, so it can never drift.
_VARIATION_SELECTOR = "\ufe0f"


def _legacy_aliases() -> dict[str, str]:
    """Map a label, its leading emoji, and their VS16-stripped forms to a command."""
    aliases: dict[str, str] = {}
    for label, data in (b for row in MENU for b in row):
        head = label.split(" ", 1)[0]
        for key in (label, head):
            for form in (key, key.replace(_VARIATION_SELECTOR, "")):
                if form:
                    aliases[form] = data
    return aliases


LEGACY_ALIASES: dict[str, str] = _legacy_aliases()


def resolve_data(raw: str) -> str:
    """Normalise a button payload or typed command to a canonical command.

    Emoji payloads from pre-existing keyboards are translated here, and the
    emoji variation selector (U+FE0F) is dropped, because Telegram may or may
    not include it — ``⚙️`` and ``⚙`` must both resolve.
    """
    key = (raw or "").strip()
    if not key:
        return key
    return (
        LEGACY_ALIASES.get(key) or LEGACY_ALIASES.get(key.replace(_VARIATION_SELECTOR, "")) or key
    )


def menu_keyboard() -> dict:
    """The inline keyboard attached to every card."""
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in MENU
        ]
    }


def reply_keyboard() -> dict:
    """The persistent panel pinned to the bottom of the chat.

    ``is_persistent`` keeps it on screen after a message is sent, and
    ``resize_keyboard`` stops it from swallowing half the screen. It is set
    once and stays for the chat, so the trader always has a button in reach
    instead of scrolling back to an old card.
    """
    return {
        "keyboard": [[{"text": label} for label in row] for row in REPLY_MENU],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def settings_keyboard(timeframes: tuple[str, ...], min_rr: float) -> dict:
    """The settings grid: frame toggles, sensitivity, and a way back.

    A frame is shown ticked when it is read. Sensitivity shows a tick on the
    option whose value matches the current minimum reward-to-risk.
    """
    active = set(timeframes)
    frame_rows: list[list[dict]] = [[], []]
    for index, tf in enumerate(ALL_TIMEFRAMES):
        frame_rows[index // 4].append(
            {
                "text": f"{'✅' if tf in active else '⬜'} {tf}",
                "callback_data": f"tf:{tf}",
            }
        )
    sens_row = [
        {
            "text": f"{'✅ ' if abs(min_rr - value) < 1e-9 else ''}{label}",
            "callback_data": f"sens:{key}",
        }
        for key, label, value in SENSITIVITY
    ]
    return {
        "inline_keyboard": [
            *[row for row in frame_rows if row],
            sens_row,
            [
                {"text": "🔄 تحديث", "callback_data": "refresh"},
                {"text": "↩️ رجوع", "callback_data": "menu"},
            ],
        ]
    }
