"""Tests for the macro context layer.

The macro layer is the one place this project measured and then chose *not* to
trade: the gold/dollar link is real (level correlation about -0.6) but
contemporaneous, and gating momentum on it made the result worse. So the tests
that matter are not "does it fetch" but the honesty ones:

  * a failed read must be reported, never rendered as a calm macro;
  * a cached-but-stale number must not be served as clean;
  * a quiet macro says ``unknown``, which is not the same claim as ``neutral``;
  * a plan leaning into the wind is named as beta, not dressed up as a signal.
"""

from __future__ import annotations

import urllib.error

import pytest

from goldbot import pipeline as pipeline_mod
from goldbot.news import macro as macro_mod
from goldbot.news.macro import (
    HEADWIND,
    MIXED,
    NEUTRAL,
    TAILWIND,
    UNKNOWN,
    MacroFeed,
    _change,
    _vote_for_dollar,
    _vote_for_euro,
    _vote_for_real_yield,
    alignment,
    combine_votes,
    macro_context,
    parse_fred_csv,
)
from goldbot.telegram.format import format_news, format_plan, format_status, macro_lines


class _Resp:
    """A minimal stand-in for the object ``open_url`` yields."""

    def __init__(self, body: bytes, status: int = 200):
        self.status = status
        self._body = body

    def read(self, *_):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── parsing ─────────────────────────────────────────────────────────────


def test_fred_csv_skips_holiday_rows_and_sorts():
    text = "observation_date,DFII10\n2026-10-05,2.20\n2026-10-01,2.10\n2026-10-02,.\n"
    rows = parse_fred_csv(text)
    # The '.' holiday row is dropped, not read as zero, and order is oldest first.
    assert [r[1] for r in rows] == [2.10, 2.20]


def test_fred_csv_ignores_a_bad_date_or_a_short_row():
    text = "d,v\nnot-a-date,1.0\n2026-10-01\n2026-10-02,3.0\n"
    assert [r[1] for r in parse_fred_csv(text)] == [3.0]


def test_change_needs_enough_observations():
    rows = [(1, 1.0), (2, 2.0)]
    assert _change(rows, obs=5) is None
    assert _change([(i, float(i)) for i in range(7)], obs=5) == 5.0


# ── voting ──────────────────────────────────────────────────────────────


def test_vote_helpers_respect_their_floors():
    # A move inside the floor is noise, not a direction.
    assert _vote_for_euro(0.05, 0.15) == NEUTRAL
    assert _vote_for_euro(0.5, 0.15) == TAILWIND
    assert _vote_for_euro(-0.5, 0.15) == HEADWIND
    assert _vote_for_dollar(None, 0.3) == NEUTRAL
    assert _vote_for_dollar(0.9, 0.3) == HEADWIND  # stronger dollar pressures gold
    assert _vote_for_real_yield(0.2, 0.1) == HEADWIND
    assert _vote_for_real_yield(-0.2, 0.1) == TAILWIND


def test_combine_votes_is_a_majority_and_says_when_it_cannot():
    assert combine_votes({"a": TAILWIND, "b": TAILWIND, "c": HEADWIND}) == TAILWIND
    assert combine_votes({"a": HEADWIND, "b": HEADWIND, "c": TAILWIND}) == HEADWIND
    # An even split is stated, not resolved by picking a side.
    assert combine_votes({"a": TAILWIND, "b": HEADWIND}) == MIXED
    # No non-neutral vote is "nothing to say", not "no net lean".
    assert combine_votes({"a": NEUTRAL, "b": NEUTRAL}) == UNKNOWN


def test_alignment_names_the_wind_a_plan_fights():
    assert alignment(HEADWIND, "buy") == "against"
    assert alignment(HEADWIND, "sell") == "with"
    assert alignment(TAILWIND, "buy") == "with"
    assert alignment(TAILWIND, "sell") == "against"
    assert alignment(UNKNOWN, "buy") == ""
    assert alignment(HEADWIND, "none") == ""


# ── the feed ────────────────────────────────────────────────────────────


def _feed(fred_ok=True, fx_ok=True):
    """A MacroFeed with both legs stubbed, returning predictable numbers.

    ``fred_ok``/``fx_ok`` False make that leg fail, so a partial and a total
    failure can both be exercised. The FRED stub answers per series, because a
    single shared value would make every vote identical and the bias untestable.
    """
    f = MacroFeed()
    calls = {"fred": 0, "fx": 0}
    # (last value, change over 5 obs): real yield up 0.6pp (headwind), dollar
    # flat (neutral), inflation irrelevant to the vote.
    rows = {
        "DFII10": [(i, 2.0) for i in range(5)] + [(5, 2.6)],
        "DTWEXBGS": [(i, 121.0) for i in range(6)],
        "T10YIE": [(i, 2.3) for i in range(6)],
    }

    def _fred(series, start):
        calls["fred"] += 1
        if not fred_ok:
            return [], "HTTP 429"
        return rows[series], ""

    def _fx():
        calls["fx"] += 1
        if not fx_ok:
            return {}, "HTTP 500"
        return {"close": 1.1, "change_1d_pct": 0.4, "change_5d_pct": -0.2}, ""

    f._fetch_fred = _fred
    f._fetch_fx = _fx
    return f, calls


def test_a_good_read_reports_ok_and_a_bias():
    f, _ = _feed()
    ctx = f.context()
    assert f.ok is True and f.error == ""
    assert ctx.checked is True and ctx.stale is False
    assert ctx.real_yield_10y == 2.6
    assert ctx.fx_change_1d_pct == 0.4
    # euro up (tailwind) vs real yield up (headwind); the dollar is inside its
    # floor so it abstains → an even split, stated as mixed.
    assert ctx.bias == MIXED
    assert ctx.votes["dollar_trend"] == NEUTRAL


def test_a_failed_leg_is_reported_not_swallowed():
    f, _ = _feed(fx_ok=False)
    ctx = f.context()
    assert f.ok is False
    assert "HTTP 500" in f.error
    # The read is partial: still checked, but flagged stale.
    assert ctx.checked is True and ctx.stale is True


def test_a_total_failure_is_not_claimed_as_checked():
    f, _ = _feed(fred_ok=False, fx_ok=False)
    ctx = f.context()
    assert f.ok is False
    assert ctx.checked is False
    assert ctx.bias == UNKNOWN


def test_a_stale_number_is_never_served_as_clean():
    """The calendar's bug, guarded here: a kept value after a failure must not
    come back on the next call as a healthy read."""
    f, calls = _feed()
    f.context(now=1000.0)  # first call: good
    f._fetch_fred = lambda series, start: ([], "HTTP 429")  # now it breaks
    ctx = f.context(now=1000.0 + 6 * 3600 + 1)  # past the FRED TTL, so it refetches
    assert ctx.real_yield_10y == 2.6  # previous number kept
    assert f.ok is False  # but the error is reported, not hidden
    assert "HTTP 429" in f.error


def test_a_failure_backs_off_instead_of_hammering():
    f, calls = _feed(fred_ok=False)
    f.context(now=1000.0)
    first = calls["fred"]
    f.context(now=1000.0 + 5.0)  # well inside NEGATIVE_TTL
    assert calls["fred"] == first  # no second attempt


def test_a_good_read_is_cached_within_its_ttl():
    f, calls = _feed()
    f.context(now=1000.0)
    assert calls["fred"] == 3  # one attempt per series
    f.context(now=1000.0 + 60.0)  # inside FRED_TTL
    assert calls["fred"] == 3  # served from cache


def test_macro_context_never_raises(monkeypatch):
    class _Boom:
        def context(self, now=None):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(macro_mod, "_FEED", _Boom())
    out = macro_context()
    assert out["checked"] is False
    assert "kaboom" in out["error"]


# ── the real fetch paths ────────────────────────────────────────────────


def test_fetch_fred_parses_a_real_response(monkeypatch):
    body = b"observation_date,DFII10\n2026-10-01,2.10\n2026-10-02,2.30\n"
    monkeypatch.setattr(macro_mod, "open_url", lambda req, timeout=None: _Resp(body))
    rows, error = MacroFeed()._fetch_fred("DFII10", "2026-01-01")
    assert error == "" and [r[1] for r in rows] == [2.10, 2.30]


def test_fetch_fred_reports_an_http_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)

    monkeypatch.setattr(macro_mod, "open_url", boom)
    rows, error = MacroFeed()._fetch_fred("DFII10", "2026-01-01")
    assert rows == [] and error == "HTTP 429"


def test_fetch_fx_computes_changes_from_klines(monkeypatch):
    import json

    rows = [[i, "0", "0", "0", str(1.0 + i / 100), "0"] for i in range(6)]
    monkeypatch.setattr(
        macro_mod, "open_url", lambda req, timeout=None: _Resp(json.dumps(rows).encode())
    )
    values, error = MacroFeed()._fetch_fx()
    assert error == ""
    assert values["change_1d_pct"] is not None
    assert values["change_5d_pct"] is not None


def test_fetch_fx_refuses_a_thin_payload(monkeypatch):
    monkeypatch.setattr(macro_mod, "open_url", lambda req, timeout=None: _Resp(b"[]"))
    values, error = MacroFeed()._fetch_fx()
    assert values == {} and error == "unexpected payload"


def test_fetch_fred_reports_a_non_200(monkeypatch):
    monkeypatch.setattr(macro_mod, "open_url", lambda req, timeout=None: _Resp(b"", status=503))
    rows, error = MacroFeed()._fetch_fred("DFII10", "2026-01-01")
    assert rows == [] and error == "HTTP 503"


def test_fetch_fred_reports_a_network_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(macro_mod, "open_url", boom)
    rows, error = MacroFeed()._fetch_fred("DFII10", "2026-01-01")
    assert rows == [] and error == "URLError"


def test_fetch_fx_reports_a_non_200(monkeypatch):
    monkeypatch.setattr(macro_mod, "open_url", lambda req, timeout=None: _Resp(b"", status=502))
    values, error = MacroFeed()._fetch_fx()
    assert values == {} and error == "HTTP 502"


def test_fetch_fx_reports_a_network_error(monkeypatch):
    def boom(req, timeout=None):
        raise urllib.error.HTTPError("u", 418, "teapot", {}, None)

    monkeypatch.setattr(macro_mod, "open_url", boom)
    values, error = MacroFeed()._fetch_fx()
    assert values == {} and error == "HTTP 418"


def test_a_series_with_no_rows_is_reported_as_empty(monkeypatch):
    """A 200 with an empty body is a failure, not a zero — reading it as zero
    would show a violent move that never happened."""
    f = MacroFeed()
    monkeypatch.setattr(f, "_fetch_fred", lambda series, start: ([], ""))
    monkeypatch.setattr(f, "_fetch_fx", lambda: ({"change_1d_pct": 0.0, "change_5d_pct": 0.0}, ""))
    ctx = f.context()
    assert f.ok is False
    assert "empty" in f.error
    assert ctx.real_yield_10y is None


def test_close_returns_none_for_a_malformed_row():
    assert macro_mod._close(["t", "o", "h", "l"]) is None
    assert macro_mod._close(["t", "o", "h", "l", "not-a-number"]) is None


def test_day_of_rejects_a_bad_date():
    with pytest.raises(ValueError):
        macro_mod._day_of("not-a-date")


def test_a_sell_into_dollar_weakness_is_named_as_beta():
    lines = macro_lines({"macro": _macro(bias=TAILWIND)}, "sell")
    assert "بيتا على الذهب" in "\n".join(lines)


# ── the cards ───────────────────────────────────────────────────────────


def _macro(**over):
    base = {
        "checked": True,
        "stale": False,
        "bias": HEADWIND,
        "fx_change_1d_pct": -0.31,
        "real_yield_10y": 2.64,
        "real_yield_change": 0.14,
        "broad_dollar": 121.3,
        "broad_dollar_change": 0.42,
    }
    base.update(over)
    return base


def test_a_buy_into_dollar_strength_is_named_as_beta():
    lines = macro_lines({"macro": _macro()}, "buy")
    text = "\n".join(lines)
    assert "ريح معاكسة" in text
    assert "بيتا على الذهب" in text  # named honestly, not sold as a signal


def test_a_sell_with_the_wind_carries_no_warning():
    text = "\n".join(macro_lines({"macro": _macro()}, "sell"))
    assert "بيتا على الذهب" not in text


def test_an_unchecked_macro_never_reads_as_calm():
    text = "\n".join(macro_lines({"macro": {"checked": False, "error": "HTTP 429"}}))
    assert "تعذّر الفحص" in text
    assert "ريح" not in text


def test_no_macro_key_adds_nothing():
    assert macro_lines({}) == []
    assert macro_lines({"macro": {}}) == []


def test_the_plan_card_carries_the_macro_context():
    plan = {"direction": "buy", "entry": 4010.0, "stop": 3995.0, "target": 4050.0, "rr": 2.67}
    card = format_plan(plan, None, {"macro": _macro(), "blackout": {}})
    assert "الماكرو" in card and "بيتا على الذهب" in card


def test_the_status_card_carries_the_macro_context():
    card = format_status({"direction": "none"}, None, {"macro": _macro(), "blackout": {}})
    assert "الماكرو" in card


def test_the_news_card_prints_the_numbers_and_the_caveat():
    card = format_news(
        {"source": {"calendar_ok": True}, "blackout": {}, "upcoming": [], "macro": _macro()}
    )
    assert "الماكرو (سياق لا إشارة)" in card
    assert "EURUSD" in card
    assert "متزامن لا سابق" in card  # the caveat travels with the number


def test_the_news_card_says_so_when_the_macro_could_not_be_read():
    card = format_news(
        {
            "source": {"calendar_ok": True},
            "blackout": {},
            "upcoming": [],
            "macro": {"checked": False, "error": "HTTP 429"},
        }
    )
    assert "تعذّر الفحص" in card


# ── wiring ──────────────────────────────────────────────────────────────


def test_the_prompt_carries_macro_only_when_it_was_checked():
    from goldbot.ai.prompt import build_prompt

    facts = {"symbol": "XAUUSD", "confluence": {}, "timeframes": {}}
    with_macro = build_prompt(facts, {"macro": _macro()})
    assert "macro" in with_macro
    without = build_prompt(facts, {"macro": {"checked": False, "error": "x"}})
    assert "macro" not in without  # silence must not read as "macro is calm"


def test_the_digest_includes_macro_and_reports_its_flags(monkeypatch):
    from goldbot.news import window as window_mod

    monkeypatch.setattr(window_mod, "macro_context", lambda now=None: _macro())
    out = window_mod.digest(now=1000.0)
    assert out["macro"]["bias"] == HEADWIND
    assert out["source"]["macro_ok"] is True


def test_the_digest_omits_macro_when_disabled(monkeypatch):
    from goldbot.news import window as window_mod

    monkeypatch.setattr(window_mod, "macro_context", lambda now=None: _macro())
    out = window_mod.digest(now=1000.0, with_macro=False)
    assert out["macro"] == {}


def test_the_pipeline_passes_the_macro_flag_through(monkeypatch):
    """``enable_macro`` on settings must reach the digest, or the flag is a lie."""
    seen = {}

    def _digest(now, with_macro=True):
        seen["with_macro"] = with_macro
        return {"blackout": {"active": False}}

    monkeypatch.setattr(pipeline_mod, "news_digest", _digest)
    monkeypatch.setattr(pipeline_mod, "build_facts", lambda s, sym, now: _Sheet())
    monkeypatch.setattr(pipeline_mod, "build_plan", lambda f, rr: _Plan())

    pipeline_mod.Pipeline(_Settings(enable_macro=False), feed=_Feed()).run(
        with_ai=False, with_news=True
    )
    assert seen["with_macro"] is False


class _Settings:
    symbol = "XAUUSD"
    timeframes = ("H1",)
    bars = 60
    min_rr = 2.0
    cache_seconds = 0
    groq_key = ""

    def __init__(self, enable_macro=True):
        self.enable_macro = enable_macro


class _Sheet:
    sources = {"H1": "stub"}

    def as_dict(self):
        return {"symbol": "XAUUSD", "timeframes": {"H1": {}}, "confluence": {}}


class _Plan:
    direction = "none"

    def as_dict(self):
        return {"direction": "none"}


class _Feed:
    def series(self, tf, bars):
        from goldbot.models import Bar, Series

        bars_ = [Bar(time=1000 + i * 60, open=1.0, high=2.0, low=0.5, close=1.5) for i in range(60)]
        return Series(timeframe=tf, bars=bars_, source="stub"), _Report()

    def spot_check(self, series):
        return None


class _Report:
    chosen = "stub"
    attempts = [("stub", "ok")]


def test_settings_has_the_macro_flag_on_by_default(monkeypatch):
    monkeypatch.delenv("GOLDBOT_ENABLE_MACRO", raising=False)
    from goldbot.config import Settings

    assert Settings().enable_macro is True


def test_settings_macro_flag_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("GOLDBOT_ENABLE_MACRO", "0")
    from goldbot.config import Settings

    assert Settings().enable_macro is False
