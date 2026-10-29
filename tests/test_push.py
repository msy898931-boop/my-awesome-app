"""Tests for the automatic push: its cost, its frequency, and its sources.

Two bugs these cover, both found by reading the live bot log:

  * ``plan_key`` included entry/stop/target, which drift by a tick on every
    read, so one idea looked new on nearly every cycle — the log showed the
    same "buy" sent eight times in forty minutes (~250/day, 4.3x the daily
    token budget).
  * the RSS labels named sources that did not answer: the "Reuters" URL is
    WSJ's feed, and news_301 is Investing's *crypto* stream, the wrong asset
    entirely for a gold bot.
"""

from __future__ import annotations

import os
import tempfile

from goldbot.config import Settings
from goldbot.news.rss import FEEDS
from goldbot.pipeline import Result
from goldbot.telegram.bot import Bot
from goldbot.telegram.format import format_price


def _plan(entry, stop, target, direction="buy", setup="CHoCH + FVG"):
    return Result(
        generated_at=0,
        symbol="XAUUSD",
        plan={
            "direction": direction,
            "entry": entry,
            "stop": stop,
            "target": target,
            "setup": setup,
        },
    )


# ── frequency: an idea is not its numbers ────────────────────────────────


def test_a_drifting_price_is_the_same_idea():
    drift = [
        _plan(4046.31, 4040.24, 4061.73),
        _plan(4047.10, 4040.90, 4062.5),
        _plan(4045.80, 4039.70, 4060.9),
        _plan(4048.02, 4041.60, 4063.4),
    ]
    keys = {Bot.plan_key(r) for r in drift}
    assert keys == {"buy:CHoCH + FVG"}


def test_a_new_setup_is_a_new_idea():
    assert Bot.plan_key(_plan(1, 0.9, 1.2, setup="CHoCH + FVG")) != Bot.plan_key(
        _plan(1, 0.9, 1.2, setup="BOS continuation")
    )


def test_the_opposite_direction_is_a_new_idea():
    assert Bot.plan_key(_plan(1, 0.9, 1.2, direction="buy")) != Bot.plan_key(
        _plan(1, 1.1, 0.8, direction="sell")
    )


def test_no_trade_is_its_own_key():
    assert (
        Bot.plan_key(Result(generated_at=0, symbol="XAUUSD", plan={"direction": "none"}))
        == "no-trade"
    )


# ── cost: the automatic push must not call the model ─────────────────────


def test_tick_does_not_ask_the_model(monkeypatch):
    """The push is deterministic, so a cycle must cost zero tokens."""
    calls: list[bool] = []
    settings = Settings(groq_key="k")
    bot = Bot(settings, state_path=os.path.join(tempfile.mkdtemp(), "s.json"), tg=_Recorder())
    bot._log = lambda m: None
    real_run = bot.pipeline.run

    def spy(with_ai=True, with_news=True, max_age=None):
        calls.append(with_ai)
        return real_run(with_ai=with_ai, with_news=with_news, max_age=max_age)

    bot.pipeline.run = spy
    bot._primed = True
    bot._last_key = "different"
    bot.tick()
    assert calls and not any(calls), f"the model was asked on a push: {calls}"


def test_a_repeated_idea_sends_once(monkeypatch):
    rec = _Recorder()
    bot = Bot(Settings(groq_key=""), state_path=os.path.join(tempfile.mkdtemp(), "s.json"), tg=rec)
    bot._log = lambda m: None
    bot._primed = True
    bot._last_key = "buy:CHoCH + FVG"

    same = Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": 4046.0},
        plan={
            "direction": "buy",
            "entry": 4046.31,
            "stop": 4040.24,
            "target": 4061.73,
            "setup": "CHoCH + FVG",
        },
    )
    bot.pipeline.run = lambda **k: same
    bot.tick()
    assert rec.sent == [], "a re-priced idea was pushed again"


# ── shape: what the automatic push actually says ─────────────────────────


def test_the_price_card_leads_with_the_price():
    card = format_price({"close": 4048.43}, {"direction": "buy", "setup": "CHoCH + FVG"}, {})
    assert "4048.43" in card
    assert "شراء" in card
    assert "تحليل استشاري" in card


def test_the_price_card_warns_on_a_news_window():
    card = format_price({"close": 4048.0}, {}, {"blackout": {"active": True}})
    assert "نافذة خبر" in card


def test_the_price_card_names_the_next_release_when_calm():
    card = format_price(
        {"close": 4048.0}, {}, {"upcoming": [{"title": "Federal Funds Rate", "in_minutes": 306.0}]}
    )
    assert "Federal Funds Rate" in card
    assert "بعد 306 د" in card


def test_the_price_card_survives_a_missing_quote():
    card = format_price(None, {}, {})
    assert "غير متاح" in card


# ── blackout: the rule is enforced, not merely printed ───────────────────


def _bot(rec):
    bot = Bot(Settings(groq_key=""), state_path=os.path.join(tempfile.mkdtemp(), "s.json"), tg=rec)
    bot._log = lambda m: None
    bot._primed = True
    return bot


def _idea(**news):
    return Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": 4046.0},
        plan={
            "direction": "buy",
            "entry": 4046.31,
            "stop": 4040.24,
            "target": 4061.73,
            "setup": "CHoCH + FVG",
        },
        news=news,
    )


def test_a_blackout_withholds_the_idea_but_still_sends_the_price():
    """The docstring promised no new idea in the window; this makes it true."""
    rec = _Recorder()
    bot = _bot(rec)
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea(blackout={"active": True})
    bot.tick()
    assert len(rec.sent) == 1, "the price card must still go out"
    card = rec.sent[0]
    assert "4046.0" in card, "the price is still delivered"
    assert "شراء" not in card, "the direction is withheld inside the window"
    assert "نافذة خبر" in card


def test_the_withheld_idea_is_delivered_once_the_window_closes():
    rec = _Recorder()
    bot = _bot(rec)
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea(blackout={"active": True})
    bot.tick()
    assert len(rec.sent) == 1 and "شراء" not in rec.sent[0]

    # The window closes; the same idea must now be delivered, not swallowed.
    bot.pipeline.run = lambda **k: _idea(blackout={"active": False})
    bot.tick()
    assert len(rec.sent) == 2
    assert "شراء" in rec.sent[1]


def test_a_no_trade_read_is_never_withheld():
    """A price-only read has no idea to withhold, blackout or not."""
    rec = _Recorder()
    bot = _bot(rec)
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: Result(
        generated_at=0,
        symbol="XAUUSD",
        spot={"close": 4046.0},
        plan={"direction": "none"},
        news={"blackout": {"active": True}},
    )
    bot.tick()
    assert rec.sent == [], "a repeated no-trade read is not a new idea"


def test_pending_key_survives_a_restart():
    rec = _Recorder()
    path = os.path.join(tempfile.mkdtemp(), "s.json")
    bot = Bot(Settings(groq_key=""), state_path=path, tg=rec)
    bot._log = lambda m: None
    bot._primed = True
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea(blackout={"active": True})
    bot.tick()

    reborn = Bot(Settings(groq_key=""), state_path=path, tg=_Recorder())
    assert reborn._pending_key == "buy:CHoCH + FVG", "the withheld idea must persist"


def test_an_unchecked_calendar_withholds_the_idea_on_the_push_too():
    """A failed calendar read must not authorise the push any more than the card.

    The digest reports ``active=False`` when the fetch failed, so a push that
    trusted only ``active`` sent the direction as if the rule had been checked.
    One predicate guards both paths, and it now fails closed."""
    rec = _Recorder()
    bot = _bot(rec)
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea(
        blackout={"active": False, "checked": False, "reason": "تعذّر فحص التقويم: HTTP 429"}
    )
    bot.tick()
    assert len(rec.sent) == 1, "the price card must still go out"
    card = rec.sent[0]
    assert "4046.0" in card, "the price is still delivered"
    assert "شراء" not in card, "the idea is withheld when the calendar could not be read"


def test_the_withheld_idea_is_delivered_when_the_calendar_reads_again():
    """The withheld idea is not lost — it goes out once the read succeeds."""
    rec = _Recorder()
    bot = _bot(rec)
    bot._last_key = "no-trade"
    bot.pipeline.run = lambda **k: _idea(blackout={"active": False, "checked": False})
    bot.tick()
    assert len(rec.sent) == 1 and "شراء" not in rec.sent[0]

    bot.pipeline.run = lambda **k: _idea(blackout={"active": False, "checked": True})
    bot.tick()
    assert len(rec.sent) == 2
    assert "شراء" in rec.sent[1]


# ── sources: the label must name the feed that answers ───────────────────


def test_no_feed_is_labelled_reuters():
    # The URL is WSJ's; calling it Reuters misreports the source.
    labels = [label for label, _ in FEEDS]
    assert "Reuters Markets" not in labels
    assert any("WSJ" in label for label in labels)


def test_the_crypto_feed_is_not_used_for_gold():
    urls = [url for _, url in FEEDS]
    assert not any("news_301" in url for url in urls)


class _Recorder:
    def __init__(self):
        self.sent = []

    def send(self, chat_id, text, markdown=True, keyboard=None):
        self.sent.append(text)
        return True

    def typing(self, *a, **k):
        return True

    def set_commands(self, *a, **k):
        return True
