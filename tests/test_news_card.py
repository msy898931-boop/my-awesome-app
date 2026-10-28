"""Tests for the rich news card.

The card the user wanted restored: the window, what is coming, what just
printed with its numbers, and the gold headlines. The parts worth pinning down
are the honest ones — a failed calendar read must not look calm, a release with
no baseline must not get an invented arrow, and the mechanical lean must match
the direction the news layer computed rather than being re-derived here.
"""

from __future__ import annotations

import time

from goldbot.news.calendar import Event, _to_float
from goldbot.telegram.format import format_news


def _released(title, actual, forecast, previous, ago_min, country="USD"):
    event = Event(title=title, country=country, impact="high",
                  time=time.time() - ago_min * 60,
                  actual=_to_float(actual), forecast=_to_float(forecast),
                  previous=_to_float(previous))
    return {**event.gold_read(), "in_minutes": round((event.time - time.time()) / 60.0, 1)}


def _card(**over):
    news = {"source": {"calendar_ok": True}, "blackout": {}, "upcoming": [],
            "released": [], "headlines": []}
    news.update(over)
    return format_news(news)


# ── the window ───────────────────────────────────────────────────────────

def test_a_calm_window_says_so():
    assert "السوق خالٍ من الأخبار المهمة" in _card()


def test_a_failed_read_is_not_reported_as_calm():
    card = _card(source={"calendar_ok": False, "calendar_error": "429"})
    assert "تعذّر فحص التقويم" in card
    assert "السوق خالٍ" not in card
    assert "429" in card


def test_an_active_window_names_the_event():
    card = _card(blackout={"active": True, "imminent": [
        {"title": "FOMC Statement", "country": "USD", "minutes": -1.0}]})
    assert "نافذة خبر عالي التأثير الآن" in card
    assert "FOMC Statement" in card
    assert "السوق خالٍ" not in card


# ── upcoming ─────────────────────────────────────────────────────────────

def test_upcoming_shows_minutes_and_country():
    card = _card(upcoming=[{"title": "GDP", "country": "US", "in_minutes": 59.0}])
    assert "أحداث قادمة عالية التأثير" in card
    assert "GDP [US] بعد 59 د" in card


def test_minutes_are_never_shown_as_a_float():
    card = _card(upcoming=[{"title": "GDP", "country": "US", "in_minutes": 51.0}])
    assert "51.0" not in card
    assert "بعد 51 د" in card


# ── released ─────────────────────────────────────────────────────────────

def test_released_shows_actual_forecast_and_previous():
    card = _card(released=[_released("ثقة المستهلك", "91.7", "81", "84.2", 1)])
    assert "صدر للتو" in card
    assert "الفعلي 91.7 · التقدير 81 · السابق 84.2" in card


def test_a_hot_us_print_pressures_gold():
    card = _card(released=[_released("ثقة المستهلك", "91.7", "81", "84.2", 1)])
    assert "الفعلي أعلى → USD أقوى → ضغط على الذهب" in card


def test_a_cold_us_print_supports_gold():
    card = _card(released=[_released("New Home Sales MoM", "-0.6", None, "6.4", 1)])
    assert "الفعلي أدنى → USD أضعف → دعم للذهب" in card


def test_an_inline_print_claims_no_direction():
    card = _card(released=[_released("GDP", "2.0", "2.0", "1.0", 1)])
    assert "مطابق للتقدير" in card
    assert "ضغط على الذهب" not in card
    assert "دعم للذهب" not in card


def test_a_release_without_a_baseline_invents_no_arrow():
    card = _card(released=[_released("Some Release", "5", None, None, 1)])
    assert "→" not in card.split("صدر للتو")[1]
    assert "لا يُستنتج اتجاه" in card


def test_a_whole_number_is_not_printed_with_a_decimal():
    card = _card(released=[_released("New Home Sales", "676", "0.65", "684", 1)])
    assert "الفعلي 676 ·" in card
    assert "676.0" not in card


def test_a_non_us_release_is_labelled_indirect():
    card = _card(released=[_released("ECB Rate", "4.0", "3.5", "3.5", 1, country="EU")])
    assert "أثر غير مباشر" in card
    assert "ضغط على الذهب" not in card


# ── headlines and the honesty footer ─────────────────────────────────────

def test_headlines_show_their_source():
    card = _card(headlines=[{"title": "Gold holds $4000", "source": "WSJ Markets"}])
    assert "عناوين الذهب" in card
    assert "Gold holds $4000 — WSJ Markets" in card


def test_the_mechanical_read_is_never_sold_as_an_edge():
    assert "لا يُدَّعى حافة" in _card()
