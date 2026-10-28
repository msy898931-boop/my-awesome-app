"""Tests for the pipeline: the one path every surface goes through.

``Pipeline._read`` decides what a read *is* — which frames contributed, what the
news layer did, whether the AI ran — and that wiring was only partly covered
(71%). Everything here is stubbed: the feed, the news digest, the fact engine
and the analyst, so the test is about the pipeline's own decisions and never the
network.
"""

from __future__ import annotations

import pytest

from goldbot import pipeline as pipeline_mod
from goldbot.models import Bar, Series
from goldbot.pipeline import Pipeline


class _Settings:
    symbol = "XAUUSD"
    timeframes = ("M15", "H1")
    bars = 60
    min_rr = 2.0
    cache_seconds = 90
    groq_key = ""
    enable_macro = True


def _series(tf: str, source: str = "stub") -> Series:
    bars = [
        Bar(time=1000 + i * 60, open=1.0, high=2.0, low=0.5, close=1.5, volume=1.0)
        for i in range(60)
    ]
    return Series(timeframe=tf, bars=bars, source=source)


class _Report:
    def __init__(self, chosen="stub", attempts=None):
        self.chosen = chosen
        self.attempts = attempts or [("stub", "ok: 60 bars")]


class _Feed:
    """Serves a scripted series per timeframe; ``None`` means 'no data'."""

    def __init__(self, per_tf: dict):
        self.per_tf = per_tf
        self.spot_calls = 0

    def series(self, tf, bars):
        series = self.per_tf.get(tf)
        if series is None:
            return None, _Report(chosen="", attempts=[("down", "error: offline")])
        return series, _Report(chosen=series.source)

    def spot_check(self, series):
        self.spot_calls += 1
        return {"close": 1.5, "quotes": {"stub": 1.5}, "max_divergence_pct": 0.0, "trusted": True}


class _Sheet:
    sources = {"H1": "stub"}

    def as_dict(self):
        return {"symbol": "XAUUSD", "timeframes": {"H1": {}}, "confluence": {"bias": "bullish"}}


class _Plan:
    direction = "none"

    def as_dict(self):
        return {"direction": "none", "rejected": ["لا إشارة"]}


class _Analysis:
    def as_dict(self):
        return {"narrative": "لا شيء", "confidence": 0}


@pytest.fixture
def wired(monkeypatch):
    """Replace every stage the pipeline calls with a deterministic stub."""
    monkeypatch.setattr(pipeline_mod, "build_facts", lambda series, symbol, now: _Sheet())
    monkeypatch.setattr(pipeline_mod, "build_plan", lambda facts, min_rr: _Plan())
    monkeypatch.setattr(
        pipeline_mod,
        "news_digest",
        lambda now, with_macro=True: {"blackout": {"active": False}, "ok": True},
    )

    def _make(feed, analyst_raises=None):
        p = Pipeline(_Settings(), feed=feed)

        class _Analyst:
            def analyse(self, facts, news, min_rr):
                if analyst_raises is not None:
                    raise analyst_raises
                return _Analysis()

        p.analyst = _Analyst()
        return p

    return _make


# ── happy path ──────────────────────────────────────────────────────────


def test_a_full_read_populates_every_section(wired):
    feed = _Feed({"M15": _series("M15"), "H1": _series("H1")})
    result = wired(feed).run(with_ai=True, with_news=True)
    assert result.facts and result.news and result.plan and result.analysis
    assert result.spot is not None
    assert result.errors == []
    assert result.actionable is False


def test_the_h1_frame_is_used_for_the_spot_check(wired):
    feed = _Feed({"M15": _series("M15"), "H1": _series("H1")})
    wired(feed).run(with_ai=False, with_news=False)
    assert feed.spot_calls == 1


def test_a_read_with_no_ai_leaves_the_analysis_empty(wired):
    feed = _Feed({"H1": _series("H1")})
    result = wired(feed).run(with_ai=False, with_news=False)
    assert result.analysis == {}
    assert result.facts


# ── degradation ─────────────────────────────────────────────────────────


def test_a_frame_with_no_data_is_recorded_not_fatal(wired):
    feed = _Feed({"M15": None, "H1": _series("H1")})
    result = wired(feed).run(with_ai=False, with_news=False)
    assert any("M15" in e for e in result.errors)
    assert any(n["timeframe"] == "M15" for n in result.feed_notes)
    assert result.facts


def test_no_frame_at_all_short_circuits_with_a_reason(wired):
    feed = _Feed({"M15": None, "H1": None})
    result = wired(feed).run(with_ai=False, with_news=False)
    assert result.facts == {}
    assert result.plan == {}
    assert any("لا يوجد أي فريم" in e for e in result.errors)


def test_a_news_failure_is_captured_and_does_not_sink_the_read(wired, monkeypatch):
    def _boom(now, with_macro=True):
        raise RuntimeError("calendar down")

    monkeypatch.setattr(pipeline_mod, "news_digest", _boom)
    feed = _Feed({"H1": _series("H1")})
    result = wired(feed).run(with_ai=False, with_news=True)
    assert result.facts  # the read still stands
    assert any("news: RuntimeError" in e for e in result.errors)


def test_news_is_skipped_when_not_requested(wired):
    feed = _Feed({"H1": _series("H1")})
    result = wired(feed).run(with_ai=False, with_news=False)
    assert result.news == {}


# ── caching ─────────────────────────────────────────────────────────────


def test_a_repeat_read_inside_the_window_reuses_the_result(wired):
    feed = _Feed({"H1": _series("H1")})
    p = wired(feed)
    first = p.run(with_ai=False, with_news=False)
    second = p.run(with_ai=False, with_news=False)
    assert first is second


def test_the_cache_is_keyed_on_the_flags(wired):
    """A no-news read must not be served to a caller that asked for news."""
    feed = _Feed({"H1": _series("H1")})
    p = wired(feed)
    plain = p.run(with_ai=False, with_news=False)
    with_news = p.run(with_ai=False, with_news=True)
    assert plain is not with_news
    assert with_news.news


def test_max_age_zero_bypasses_the_cache(wired):
    feed = _Feed({"H1": _series("H1")})
    p = wired(feed)
    first = p.run(with_ai=False, with_news=False)
    second = p.run(with_ai=False, with_news=False, max_age=0)
    assert first is not second


# ── analyse: enrich, do not re-read ─────────────────────────────────────


def test_analyse_attaches_the_narrative(wired):
    feed = _Feed({"H1": _series("H1")})
    p = wired(feed)
    result = p.run(with_ai=False, with_news=False)
    assert result.analysis == {}
    p.analyse(result)
    assert result.analysis.get("narrative")


def test_analyse_is_a_no_op_when_analysis_already_exists(wired):
    feed = _Feed({"H1": _series("H1")})
    p = wired(feed)
    result = p.run(with_ai=True, with_news=False)
    before = dict(result.analysis)
    p.analyse(result)
    assert result.analysis == before


def test_analyse_is_a_no_op_without_facts(wired):
    feed = _Feed({"M15": None, "H1": None})
    p = wired(feed)
    result = p.run(with_ai=False, with_news=False)
    p.analyse(result)  # must not raise on an empty read
    assert result.analysis == {}


def test_run_once_uses_fresh_defaults(wired, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "build_feed", lambda settings: _Feed({"H1": _series("H1")}))
    result = pipeline_mod.run_once(_Settings(), with_ai=False, with_news=False)
    assert result.symbol == "XAUUSD"


# ── result document ─────────────────────────────────────────────────────


def test_actionable_is_true_only_for_a_real_direction():
    from goldbot.pipeline import Result

    assert Result(generated_at=0, symbol="X", plan={"direction": "buy"}).actionable is True
    assert Result(generated_at=0, symbol="X", plan={"direction": "none"}).actionable is False
    assert Result(generated_at=0, symbol="X").actionable is False


def test_as_dict_surfaces_the_actionable_flag():
    from goldbot.pipeline import Result

    out = Result(generated_at=0, symbol="X", plan={"direction": "sell"}).as_dict()
    assert out["actionable"] is True
    assert out["symbol"] == "X"
