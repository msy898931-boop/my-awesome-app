"""Tests for the unprompted behaviour: the news heads-up and the AI budget.

The gap these close: the bot was entirely reactive. The calendar, the window,
and the schedule only reached the trader if they pressed a button, and the push
carried a direction without the entry, stop and target — so the one thing a
trader cannot look up themselves (a release is due in half an hour) was the one
thing the bot never said, and the "trade" it did send was not actionable.

The alert decision is a pure function in ``telegram.alerts``, so it is tested
directly and with no clock. The budget is tested on the bot, because that is
where the call is made.
"""

from __future__ import annotations

import os
import tempfile
import time

from goldbot.config import Settings
from goldbot.pipeline import Result
from goldbot.telegram.alerts import alert_key, due_news_alerts, prune
from goldbot.telegram.bot import Bot
from goldbot.telegram.format import format_alert, format_news


class _Recorder:
    def __init__(self, send_ok=True):
        self.sent: list[str] = []
        self._send_ok = send_ok

    def send(self, chat_id, text, markdown=True, keyboard=None):
        self.sent.append(text)
        return self._send_ok

    def typing(self, *a, **k):
        return True

    def set_commands(self, *a, **k):
        return True


def _news(events, calendar_ok=True, blackout=None):
    return {
        "source": {"calendar_ok": calendar_ok},
        "blackout": blackout or {"active": False, "checked": True},
        "upcoming": events,
    }


def _event(title, minutes, country="USD", when=None, forecast=0.3, previous=0.2):
    return {
        "title": title,
        "country": country,
        "impact": "high",
        "time": when if when is not None else int(time.time() + minutes * 60),
        "in_minutes": minutes,
        "forecast": forecast,
        "previous": previous,
    }


def _bot(rec, **over):
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = Bot(Settings(telegram_chat="555", **over), state_path=path, tg=rec)
    bot._log = lambda m: None
    return bot


# ── the alert decision (pure) ────────────────────────────────────────────


def test_an_event_inside_the_lead_window_is_due():
    due = due_news_alerts(_news([_event("CPI", 20)]), 30.0, {})
    assert [e["title"] for e in due] == ["CPI"]


def test_an_event_beyond_the_lead_window_is_not_due():
    assert due_news_alerts(_news([_event("NFP", 45)]), 30.0, {}) == []


def test_an_event_already_printing_is_not_the_alert_s_job():
    """Inside the window the blackout card speaks, not the heads-up."""
    assert due_news_alerts(_news([_event("CPI", -3)]), 30.0, {}) == []


def test_a_zero_lead_disables_the_heads_up():
    assert due_news_alerts(_news([_event("CPI", 5)]), 0.0, {}) == []


def test_an_unread_calendar_announces_nothing():
    """ "No feed" is not "no news": a fabricated alert is worse than a missed one."""
    assert due_news_alerts(_news([_event("CPI", 10)], calendar_ok=False), 30.0, {}) == []


def test_an_announced_event_is_not_due_again():
    event = _event("CPI", 20)
    seen = {alert_key(event): time.time()}
    assert due_news_alerts(_news([event]), 30.0, seen) == []


def test_an_event_without_a_countdown_is_skipped():
    """No ``in_minutes`` means the clock is unknown, so it is not announced."""
    assert due_news_alerts(_news([{"title": "?", "country": "USD", "time": 1}]), 30.0, {}) == []


def test_the_nearest_event_is_reported_first():
    due = due_news_alerts(_news([_event("NFP", 25), _event("CPI", 8)]), 30.0, {})
    assert [e["title"] for e in due] == ["CPI", "NFP"]


def test_two_releases_at_the_same_minute_are_two_alerts():
    due = due_news_alerts(
        _news([_event("GDP", 12, country="USD"), _event("ECB", 12, country="EUR")]), 30.0, {}
    )
    assert {e["title"] for e in due} == {"GDP", "ECB"}


def test_the_key_is_the_event_s_identity_not_its_countdown():
    """The minute counter wobbles every read; the identity must not."""
    a = _event("CPI", 20, when=1_800_000_000)
    b = {**a, "in_minutes": 19.4}
    assert alert_key(a) == alert_key(b)


def test_the_same_title_next_month_is_a_different_event():
    this_month = _event("CPI", 20, when=1_800_000_000)
    next_month = _event("CPI", 20, when=1_802_600_000)
    assert alert_key(this_month) != alert_key(next_month)


def test_prune_forgets_old_events_but_keeps_recent_ones():
    now = time.time()
    kept = {"recent": now - 60}
    dropped = {"ancient": now - 10 * 24 * 3600}
    out = prune({**kept, **dropped}, now)
    assert "recent" in out and "ancient" not in out


# ── the alert card ───────────────────────────────────────────────────────


def test_the_alert_card_names_the_event_the_clock_and_the_numbers():
    card = format_alert(_event("CPI m/m", 20, when=1_800_000_000))
    assert "CPI m/m" in card
    assert "USD" in card
    assert "20 د" in card
    assert "التقدير" in card and "السابق" in card


def test_the_alert_card_carries_a_utc_clock_time():
    """A countdown is useless if the message is read an hour later."""
    when = 1_800_000_000
    card = format_alert(_event("CPI", 20, when=when))
    expected = time.strftime("%H:%M", time.gmtime(when))
    assert expected in card and "UTC" in card


# ── the bot fires it, exactly once ───────────────────────────────────────


def _news_result(events, **over):
    return Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": 4025.0},
        plan={"direction": "none"},
        news=_news(events, **over),
    )


def test_the_bot_announces_a_due_release_unprompted():
    rec = _Recorder()
    bot = _bot(rec)
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    bot.tick()
    assert len(rec.sent) == 1
    assert "🔔" in rec.sent[0] and "CPI" in rec.sent[0]


def test_the_bot_announces_the_same_release_only_once():
    rec = _Recorder()
    bot = _bot(rec)
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    bot.tick()
    bot.tick()
    assert len(rec.sent) == 1, "a second cycle must not repeat the heads-up"


def test_a_restart_does_not_replay_an_announced_release():
    rec = _Recorder()
    path = os.path.join(tempfile.mkdtemp(), "state.json")
    bot = Bot(Settings(telegram_chat="555"), state_path=path, tg=rec)
    bot._log = lambda m: None
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    bot.tick()
    assert len(rec.sent) == 1

    reborn = Bot(Settings(telegram_chat="555"), state_path=path, tg=_Recorder())
    reborn._log = lambda m: None
    reborn._primed = True
    reborn._last_key = "no-trade"
    reborn.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    reborn.tick()
    assert reborn._announced, "the announced key must survive the restart"


def test_a_failed_alert_is_retried_rather_than_marked_done():
    """A rejected send must not be recorded as delivered."""
    rec = _Recorder(send_ok=False)
    bot = _bot(rec)
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    bot.tick()
    assert bot._announced == {}, "an unsent alert must stay unannounced"


def test_the_first_pass_marks_live_events_without_sending_them():
    """A restart must not fire a burst for news the trader already lived through."""
    rec = _Recorder()
    bot = _bot(rec)
    bot.pipeline.run = lambda **k: _news_result([_event("CPI", 20)])
    bot.tick()  # the priming pass
    assert rec.sent == [], "the priming pass announces nothing"
    assert bot._announced, "but the live event is marked so the next pass is silent"


def test_the_news_card_stamps_the_clock_time_of_upcoming_events():
    """A countdown alone is useless if the card is read later; the clock is not."""
    when = 1_800_000_000
    card = format_news(
        {
            "source": {"calendar_ok": True},
            "blackout": {"active": False},
            "upcoming": [_event("CPI", 120, when=when)],
        }
    )
    assert time.strftime("%H:%M", time.gmtime(when)) in card


# ── the trade card carries the numbers ───────────────────────────────────


def _idea(**over):
    return Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": 4025.0},
        plan={
            "direction": "buy",
            "setup": "CHoCH + FVG",
            "entry": 4026.5,
            "stop": 4019.8,
            "target": 4042.1,
            "rr": 2.3,
            **over,
        },
        news=_news([]),
    )


def test_the_push_carries_entry_stop_and_target():
    rec = _Recorder()
    bot = _bot(rec)
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea()
    bot.tick()
    card = rec.sent[0]
    assert "4026.5" in card and "4019.8" in card and "4042.1" in card
    assert "1:2.3" in card


# ── the AI budget ────────────────────────────────────────────────────────


def test_the_model_is_asked_once_per_new_idea():
    rec = _Recorder()
    bot = _bot(rec, groq_key="test-key", ai_daily_cap=10)
    bot._primed = True
    bot._last_key = "no-trade"
    calls = []

    def fake_analyse(result):
        calls.append(result)
        result.analysis = {"reasoning": "شرح", "confidence": 0.5}
        return result

    bot.pipeline.run = lambda **k: _idea()
    bot.pipeline.analyse = fake_analyse
    bot.tick()
    bot.tick()  # same idea: not new, so no second call
    assert len(calls) == 1


def test_the_daily_cap_stops_the_model_but_not_the_trade():
    """Capped, the card loses the commentary — never the trade."""
    rec = _Recorder()
    bot = _bot(rec, groq_key="test-key", ai_daily_cap=1)
    bot._primed = True
    bot._last_key = "no-trade"
    # Spend the cap *today*: the counter is per UTC day, so both fields matter.
    bot._ai_day = time.strftime("%Y-%m-%d", time.gmtime())
    bot._ai_calls = 1
    called = []
    bot.pipeline.analyse = lambda r: called.append(r) or r
    bot.pipeline.run = lambda **k: _idea()
    bot.tick()
    assert called == [], "the model must not be asked once the cap is spent"
    assert "4026.5" in rec.sent[0], "the trade still goes out"


def test_no_key_means_no_call():
    rec = _Recorder()
    bot = _bot(rec, groq_key="")
    bot._primed = True
    bot._last_key = "no-trade"
    called = []
    bot.pipeline.analyse = lambda r: called.append(r) or r
    bot.pipeline.run = lambda **k: _idea()
    bot.tick()
    assert called == []


def test_the_counter_rolls_at_the_utc_date_change():
    rec = _Recorder()
    bot = _bot(rec, groq_key="k", ai_daily_cap=5)
    bot._ai_day = "1970-01-01"
    bot._ai_calls = 4
    assert bot._ai_today() == 0, "a new UTC day resets the count"


def test_a_fallback_read_is_not_shown_as_an_analysis():
    """A deterministic fallback has no reasoning; it must not be dressed as one."""
    rec = _Recorder()
    bot = _bot(rec, groq_key="test-key", ai_daily_cap=10)
    bot._primed = True
    bot._last_key = "no-trade"

    def fallback(result):
        result.analysis = {"reasoning": "", "confidence": 0, "error": "quota"}
        return result

    bot.pipeline.analyse = fallback
    bot.pipeline.run = lambda **k: _idea()
    bot.tick()
    assert "ثقة التقدير" not in rec.sent[0]
    assert "الشرح" not in rec.sent[0]
