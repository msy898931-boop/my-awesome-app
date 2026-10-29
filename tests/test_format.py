"""Tests for the Arabic card renderers.

``format_price`` is pinned in ``test_push.py`` and the news card in
``test_news_card.py``. What was uncovered (84%) is the *plan* card and the
*analysis* card: the confidence line, the reasons block, the blackout warning
and the rejected list, plus the ``_when`` / ``_num`` helpers that keep a bare
``None`` or a ``51.0`` out of the message. These are pure string functions, so
nothing is stubbed.
"""

from __future__ import annotations

from goldbot.telegram.format import (
    ADVISORY,
    _esc,
    _num,
    _when,
    blackout_blocks_idea,
    format_analysis,
    format_plan,
    format_status,
)


def _plan(**over):
    plan = {
        "direction": "buy",
        "setup": "CHoCH + FVG",
        "entry": 4050.0,
        "stop": 4044.0,
        "target": 4068.0,
        "rr": 3.0,
    }
    plan.update(over)
    return plan


# ── format_plan: the trade card ─────────────────────────────────────────


def test_a_plan_card_names_every_number():
    card = format_plan(_plan())
    assert "4050.0" in card and "4044.0" in card and "4068.0" in card
    assert "1:3.0" in card
    assert ADVISORY in card


def test_a_none_direction_falls_through_to_the_status_card():
    card = format_plan({"direction": "none", "rejected": ["لا شيء"]})
    assert "لا توجد صفقة الآن" in card


def test_the_confidence_line_is_shown_as_a_whole_percent():
    card = format_plan(_plan(), analysis={"confidence": 0.62})
    assert "62%" in card


def test_a_zero_confidence_omits_the_line():
    """A card with no model behind it must not print a made-up percentage."""
    card = format_plan(_plan(), analysis={"confidence": 0.0})
    assert "ثقة التقدير" not in card


def test_the_frame_bias_is_translated():
    card = format_plan(_plan(), analysis={"bias": "bearish"})
    assert "هابط" in card


def test_the_factors_block_is_rendered():
    card = format_plan(_plan(factors=["كسر البنية", "فجوة سعرية"]))
    assert "الأسباب" in card
    assert "كسر البنية" in card and "فجوة سعرية" in card


def test_the_reasoning_prefers_the_plan_reason_then_the_analysis():
    with_reason = format_plan(_plan(reason="سبب الصفقة"))
    assert "سبب الصفقة" in with_reason
    from_analysis = format_plan(_plan(), analysis={"reasoning": "تحليل النموذج"})
    assert "تحليل النموذج" in from_analysis


def test_the_market_state_is_rendered_when_present():
    card = format_plan(_plan(), analysis={"market_state": "السوق في انحياز صاعد"})
    assert "حالة السوق" in card
    assert "السوق في انحياز صاعد" in card


def test_a_blackout_withholds_the_idea_on_the_plan_card():
    """A blackout must not leak the numbers it exists to suppress."""
    card = format_plan(_plan(), news={"blackout": {"active": True}})
    assert "4050" not in card  # entry
    assert "4044" not in card  # stop
    assert "4068" not in card  # target
    assert "نافذة خبر" in card
    assert "محجوب" in card


def test_the_card_names_the_model_that_wrote_the_read():
    """Provenance: a read without its author is an opinion with no source."""
    card = format_plan(_plan(), analysis={"reasoning": "شرح", "model": "openai/gpt-oss-120b"})
    assert "openai/gpt-oss-120b" in card
    assert "المصدر" in card


def test_a_read_without_a_model_shows_no_source_line():
    """The fallback has no author, so it must not be given one."""
    card = format_plan(_plan(), analysis={"reasoning": "شرح", "model": ""})
    assert "المصدر" not in card


def test_a_blackout_also_hides_the_ai_confidence():
    card = format_plan(_plan(), analysis={"confidence": 0.71}, news={"blackout": {"active": True}})
    assert "71%" not in card
    assert "ثقة التقدير" not in card


def test_without_a_blackout_the_plan_card_still_shows_every_number():
    card = format_plan(_plan(), news={"blackout": {"active": False}})
    assert "4050" in card and "4044" in card and "4068" in card


def test_a_withheld_idea_is_not_rendered_as_a_flat_market():
    """The no-trade card must say *why* it is empty when the cause is a blackout."""
    card = format_plan(_plan(), news={"blackout": {"active": True}})
    assert "يوجد نموذج لكنه محجوب" in card


def test_the_no_trade_card_does_not_claim_a_withheld_model_when_there_is_none():
    card = format_plan({"direction": "none"}, news={"blackout": {"active": True}})
    assert "محجوب" not in card
    assert "نافذة خبر عالي التأثير" in card


def test_the_rejected_list_is_capped_at_three():
    card = format_plan(_plan(rejected=[f"سبب {i}" for i in range(6)]))
    assert "سبب 0" in card and "سبب 2" in card
    assert "سبب 3" not in card


# ── an unchecked calendar must be shown, with its warning ─────────────────
#
# The window digest sets ``active=False`` when the calendar could not be read,
# because an unread window is not an active one. The predicate first failed
# *closed* on that — an unread calendar withheld every idea — but the feed
# rate-limits by IP, so a transient 429 became a bot that showed nothing at all
# for as long as the source stayed blocked. The rule now withholds only a
# **known open window**; an unread calendar shows the idea with a warning, so
# the trader sees the plan and knows the news check is missing from the picture.
# "No window" is still never confused with "could not tell": the card says which.


def _unchecked_news(**over):
    news = {
        "blackout": {
            "active": False,
            "checked": False,
            "reason": "تعذّر فحص التقويم: HTTP 429",
        },
        "upcoming": [],
        "source": {"calendar_ok": False, "calendar_error": "HTTP 429"},
    }
    news.update(over)
    return news


def test_an_unchecked_calendar_does_not_block_the_idea():
    """An unread calendar is a missing input, not a veto — the idea is shown."""
    assert blackout_blocks_idea(_unchecked_news()) is False


def test_a_checked_calm_calendar_still_lets_the_idea_through():
    """The other half — a read that succeeded and found nothing must not block."""
    assert blackout_blocks_idea({"blackout": {"active": False, "checked": True}}) is False


def test_an_unchecked_calendar_still_shows_the_plan_numbers():
    """The numbers go out — a failed news check must not hide the trade."""
    card = format_plan(_plan(), news=_unchecked_news())
    assert "4050" in card  # entry
    assert "4044" in card  # stop
    assert "4068" in card  # target


def test_an_unchecked_calendar_says_the_read_failed_not_that_a_window_is_open():
    """A failed read and an open window are different facts; the card says which."""
    card = format_plan(_plan(), news=_unchecked_news())
    assert "تعذّر فحص التقويم" in card
    assert "نافذة خبر عالي التأثير" not in card


def test_an_unchecked_calendar_does_not_degrade_the_plan_card():
    """The trade card is *not* replaced by the no-trade read on a failed check.

    This is the regression the policy exists for: a rate-limited feed must not
    turn a live plan into "no trade", because the trader then sees nothing at all
    while the plan sits behind a blocked source."""
    card = format_plan(_plan(), news=_unchecked_news())
    assert "لا توجد صفقة" not in card, "a failed check must not degrade the card"
    assert "الدخول" in card and "الوقف" in card and "الهدف" in card
    assert "تعذّر فحص التقويم" in card, "but the failed check is still stated"


def test_a_blackout_predating_the_checked_flag_is_still_honoured():
    """News built without the flag (older callers, the prompt payload) still blocks
    on ``active`` — the flag is added, not required."""
    assert blackout_blocks_idea({"blackout": {"active": True}}) is True
    assert blackout_blocks_idea({"blackout": {"active": False}}) is False


def test_the_status_card_does_not_claim_a_block_when_only_the_read_failed():
    """A failed read is not an open window: the no-trade card must not say محجوب."""
    card = format_status(_plan(), news=_unchecked_news())
    assert "محجوب" not in card
    assert "تعذّر فحص التقويم" in card
    assert "نافذة خبر عالي التأثير" not in card


def test_the_status_card_calls_an_unchecked_calendar_unchecked_with_no_idea():
    """No idea and no read is still not a calm market."""
    card = format_status({"direction": "none"}, news=_unchecked_news())
    assert "تعذّر فحص التقويم" in card
    assert "السوق خالٍ" not in card


def test_a_checked_open_window_still_reads_as_a_window():
    """The other branch of the same card — unchanged, and now pinned."""
    card = format_status(_plan(), news={"blackout": {"active": True, "checked": True}})
    assert "نافذة خبر عالي التأثير" in card
    assert "تعذّر فحص التقويم" not in card


# ── format_status: the no-trade card ────────────────────────────────────


def test_the_status_card_shows_the_market_state_and_bias():
    card = format_status(
        {"direction": "none"}, analysis={"market_state": "هادئ", "bias": "neutral"}
    )
    assert "هادئ" in card and "محايد" in card


def test_the_status_card_lists_the_reasons():
    card = format_status({"rejected": ["فريم غير متوافق", "لا نموذج"]})
    assert "فريم غير متوافق" in card


def test_the_status_card_flags_a_blackout_and_the_next_event():
    card = format_status(
        {},
        news={
            "blackout": {"active": True},
            "upcoming": [{"title": "CPI", "country": "USD", "in_minutes": 45}],
        },
    )
    assert "نافذة خبر عالي التأثير" in card
    assert "CPI" in card and "USD" in card and "45" in card


def test_the_status_card_handles_news_without_an_active_window():
    card = format_status({}, news={"blackout": {"active": False}, "upcoming": []})
    assert "نافذة خبر" not in card


# ── format_analysis: the SMC card ───────────────────────────────────────


def _sheet():
    return {
        "confluence": {
            "bias": "bullish",
            "bullish_frames": 3,
            "bearish_frames": 2,
            "frames_read": 5,
        },
        "timeframes": {
            "M15": {
                "trend": "bullish",
                "last_event": {"type": "CHoCH", "direction": "bullish"},
                "premium_discount": {"zone": "premium"},
                "atr": 3.2,
                "last_close": 4050.0,
            }
        },
    }


def test_the_analysis_card_shows_the_frame_tally():
    card = format_analysis(_sheet())
    assert "3 صاعد" in card and "2 هابط" in card and "من 5" in card


def test_the_analysis_card_describes_each_frame():
    card = format_analysis(_sheet())
    assert "M15" in card
    assert "CHoCH" in card
    assert "premium" in card
    assert "3.2" in card


def test_the_analysis_card_appends_the_news_reason():
    card = format_analysis(_sheet(), news={"blackout": {"reason": "بيانات تضخم"}})
    assert "بيانات تضخم" in card


def test_the_analysis_card_survives_a_frame_with_no_event():
    sheet = {"timeframes": {"H1": {"trend": "bearish"}}, "confluence": {}}
    card = format_analysis(sheet)
    assert "H1" in card and ADVISORY in card


# ── helpers ─────────────────────────────────────────────────────────────


def test_when_rounds_to_whole_minutes():
    assert _when(51.0) == "51 د"
    assert _when(306.4) == "306 د"


def test_when_reads_now_only_for_a_past_event():
    # Zero is "0 د" (the release is due now); only a negative value is "الآن".
    assert _when(0) == "0 د"
    assert _when(-5) == "الآن"


def test_when_is_an_em_dash_for_a_non_number():
    assert _when(None) == "—"
    assert _when("soon") == "—"


def test_num_prints_an_em_dash_for_none_or_empty():
    assert _num(None) == "—"
    assert _num("") == "—"


def test_num_drops_a_trailing_decimal_on_a_whole_float():
    assert _num(4060.0) == "4060"


def test_num_keeps_a_fractional_value():
    assert _num(3.2) == "3.2"


def test_esc_neutralises_markdown_control_characters():
    assert _esc("a_b*c[d]`e`") == "a\\_b\\*c\\[d\\]\\`e\\`"
