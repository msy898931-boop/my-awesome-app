"""AYZR's own tests — the validation, the journal, and the report.

These run offline like the rest of the suite (``conftest`` blocks every
non-loopback socket), so no model is ever called: the decision layer is tested
by feeding it JSON directly, and the engine is driven with a fixed feed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from goldbot.ayzr.cards import format_decision, format_report
from goldbot.ayzr.client import AiBudgetError, AiClient, parse_json
from goldbot.ayzr.config import AyzrSettings
from goldbot.ayzr.decide import decide, validate_trade
from goldbot.ayzr.journal import (
    log_no_trade,
    log_signal,
    open_signals,
    read_all,
    resolve_open,
    signal_key,
)
from goldbot.ayzr.read import Read
from goldbot.ayzr.report import Stats, build_report, compute, verdict
from goldbot.models import Bar, Series

# ── fixtures ─────────────────────────────────────────────────────────────


def _facts(**over):
    frame = {
        "trend": "up",
        "last_close": 4000.0,
        "atr": 10.0,
        "last_swing_high": 4050.0,
        "last_swing_low": 3950.0,
        "fair_value_gaps": [{"top": 4010.0, "bottom": 4000.0, "filled": False}],
        "order_blocks": [{"top": 3995.0, "bottom": 3985.0, "mitigated": False}],
    }
    frame.update(over)
    return {"symbol": "XAUUSD", "timeframes": {"M15": frame}, "confluence": {"bias": "bullish"}}


def _settings(tmp_path: Path, **over) -> AyzrSettings:
    s = AyzrSettings(data_dir=tmp_path / "ayzr")
    for k, v in over.items():
        setattr(s, k, v)
    s.ensure_dirs()
    return s


def _bars(start: int, n: int, price: float = 4000.0, step: float = 0.0) -> list[Bar]:
    out = []
    for i in range(n):
        p = price + i * step
        out.append(Bar(time=start + i * 900, open=p, high=p + 5, low=p - 5, close=p, volume=1.0))
    return out


# ── validation: the arithmetic that guards the model ─────────────────────


def test_a_clean_buy_passes_validation():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 4005.0, "stop": 3990.0, "target": 4040.0}, _facts(), 2.0
    )
    assert reasons == []
    assert trade["direction"] == "buy"
    assert trade["rr"] == pytest.approx(2.333, abs=0.01)
    assert trade["validated"] is True


def test_a_stop_on_the_wrong_side_is_rejected():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 4005.0, "stop": 4010.0, "target": 4040.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("ترتيب" in r for r in reasons)


def test_a_below_floor_rr_is_rejected():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 4005.0, "stop": 4000.0, "target": 4007.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("R:R" in r for r in reasons)


def test_an_entry_in_no_zone_is_treated_as_invented():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 4020.0, "stop": 4010.0, "target": 4050.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("منطقة" in r for r in reasons)


def test_an_entry_far_from_price_is_rejected():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 3900.0, "stop": 3890.0, "target": 3930.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("بعيد" in r for r in reasons)


def test_direction_none_is_not_a_trade_and_not_a_rejection():
    trade, reasons = validate_trade({"direction": "none"}, _facts(), 2.0)
    assert trade == {} and reasons == []


def test_numbers_missing_is_rejected_cleanly():
    trade, reasons = validate_trade({"direction": "buy"}, _facts(), 2.0)
    assert trade == {} and reasons


def test_sell_ordering_is_checked_the_other_way():
    facts = _facts(fair_value_gaps=[{"top": 4010.0, "bottom": 4000.0, "filled": False}])
    trade, reasons = validate_trade(
        {"direction": "sell", "entry": 4005.0, "stop": 4020.0, "target": 3970.0}, facts, 2.0
    )
    assert reasons == [] and trade["direction"] == "sell"


# ── decide: model JSON to a Decision ─────────────────────────────────────


def test_decide_without_a_raw_reply_records_the_error_not_a_no_trade():
    d = decide(_facts(), None, error="تعذّر الوصول للنموذج")
    assert d.configured is False
    assert d.error == "تعذّر الوصول للنموذج"
    assert d.has_trade is False


def test_decide_parses_a_valid_reply():
    raw = {
        "market_state": "ضغط صاعد",
        "bias": "bullish",
        "technical": "CHoCH",
        "fundamental": "دولار ضعيف",
        "reasoning": "سبب",
        "confidence": 0.55,
        "scenarios": {"primary": {"thesis": "عودة للفجوة"}},
        "trade": {"direction": "buy", "entry": 4005.0, "stop": 3990.0, "target": 4040.0},
        "warnings": ["w1"],
    }
    d = decide(_facts(), raw, model="m1", min_rr=2.0)
    assert d.configured is True and d.model == "m1"
    assert d.has_trade is True
    assert d.confidence == pytest.approx(0.55)


def test_decide_normalises_an_unknown_bias():
    d = decide(_facts(), {"bias": "moon", "trade": {"direction": "none"}}, model="m")
    assert d.bias == "neutral"


def test_decide_clamps_confidence():
    d = decide(_facts(), {"confidence": 9.9, "trade": {"direction": "none"}}, model="m")
    assert d.confidence == 1.0


# ── parse_json tolerates a fenced reply ──────────────────────────────────


def test_parse_json_reads_a_plain_object():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_recovers_a_fenced_object():
    assert parse_json('```json\n{"a": 2}\n```') == {"a": 2}


def test_parse_json_returns_none_on_garbage():
    assert parse_json("not json at all") is None
    assert parse_json("") is None
    assert parse_json("[1,2]") is None


# ── the AI client's own budget ───────────────────────────────────────────


def test_the_daily_cap_raises_once_spent(tmp_path):
    s = _settings(tmp_path, ai_key="k", ai_daily_cap=2)
    c = AiClient(s)
    assert c.remaining_today() == 2
    c._spend()
    c._spend()
    assert c.remaining_today() == 0
    with pytest.raises(AiBudgetError):
        c._spend()


def test_the_budget_survives_a_new_client(tmp_path):
    s = _settings(tmp_path, ai_key="k", ai_daily_cap=5)
    AiClient(s)._spend()
    assert AiClient(s).calls_today() == 1


def test_no_key_means_no_call(tmp_path):
    s = _settings(tmp_path, ai_key="")
    with pytest.raises(AiBudgetError):
        AiClient(s).complete("sys", "user")


# ── the journal ──────────────────────────────────────────────────────────


def _decision():
    raw = {
        "bias": "bullish",
        "market_state": "ضغط",
        "confidence": 0.6,
        "scenarios": {"primary": {"thesis": "عودة"}},
        "trade": {"direction": "buy", "entry": 4005.0, "stop": 3990.0, "target": 4040.0},
    }
    return decide(_facts(), raw, model="m1", min_rr=2.0)


def test_log_signal_writes_a_line_with_a_control(tmp_path):
    s = _settings(tmp_path)
    rec = log_signal(s, _decision(), _facts(), price=4005.0, control_seed=1)
    assert rec is not None
    rows = read_all(s)
    assert len(rows) == 1
    assert rows[0]["kind"] == "signal"
    assert rows[0]["outcome"] is None
    assert rows[0]["control"]["direction"] in {"buy", "sell"}
    assert rows[0]["engine"] == "AYZR"


def test_log_signal_ignores_a_decision_without_a_trade(tmp_path):
    s = _settings(tmp_path)
    d = decide(_facts(), {"trade": {"direction": "none"}}, model="m")
    assert log_signal(s, d, _facts(), price=1.0) is None


def test_log_no_trade_records_the_denominator(tmp_path):
    s = _settings(tmp_path)
    d = decide(_facts(), {"trade": {"direction": "none"}, "bias": "bearish"}, model="m")
    log_no_trade(s, d, price=4000.0)
    rows = read_all(s)
    assert rows[0]["kind"] == "no_trade"


def test_control_direction_is_deterministic_for_a_seed(tmp_path):
    s = _settings(tmp_path)
    a = log_signal(s, _decision(), _facts(), price=1.0, control_seed=2)
    b = log_signal(s, _decision(), _facts(), price=1.0, control_seed=2)
    assert a["control"]["direction"] == b["control"]["direction"]


def test_signal_key_is_direction_and_setup_not_price():
    assert signal_key("buy", "CHoCH") == "buy:CHoCH"
    assert signal_key("buy", "CHoCH") == signal_key("buy", "CHoCH")


# ── resolution against real bars ─────────────────────────────────────────


def test_resolve_a_win(tmp_path):
    s = _settings(tmp_path)
    now = int(time.time()) - 10_000
    log_signal(s, _decision(), _facts(), price=4005.0, control_seed=1)
    # Rewrite the timestamp so bars can follow it.
    rows = read_all(s)
    rows[0]["logged_at"] = now
    s.log_path.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")

    class Feed:
        def series(self, tf, bars):
            # price rises through the target after entry
            tape = [
                Bar(time=now + 900 * (i + 1), open=4005, high=4045, low=4000, close=4044, volume=1)
                for i in range(3)
            ]
            return Series(timeframe="M15", bars=tape, source="test"), None

    n = resolve_open(s, Feed())
    assert n == 1
    st = compute(s)
    assert st.n == 1 and st.wins == 1


def test_resolve_a_loss(tmp_path):
    s = _settings(tmp_path)
    now = int(time.time()) - 10_000
    log_signal(s, _decision(), _facts(), price=4005.0, control_seed=1)
    rows = read_all(s)
    rows[0]["logged_at"] = now
    s.log_path.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")

    class Feed:
        def series(self, tf, bars):
            tape = [
                Bar(time=now + 900 * (i + 1), open=4005, high=4006, low=3985, close=3986, volume=1)
                for i in range(3)
            ]
            return Series(timeframe="M15", bars=tape, source="test"), None

    resolve_open(s, Feed())
    assert compute(s).losses == 1


def test_resolve_skips_a_signal_with_too_few_bars(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=4005.0)

    class Feed:
        def series(self, tf, bars):
            return Series(timeframe="M15", bars=[], source="t"), None

    assert resolve_open(s, Feed()) == 0


def test_open_signals_lists_unresolved(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    assert len(open_signals(s)) == 1


# ── the report and its verdict ───────────────────────────────────────────


def test_report_of_an_empty_journal(tmp_path):
    s = _settings(tmp_path)
    rep = build_report(s)
    assert rep.total.n == 0
    assert "لا إشارات" in format_report(rep)


def test_verdict_refuses_a_small_sample():
    st = Stats(n=5, wins=4, losses=1, mean_r=0.5, control_n=5, control_mean_r=0.1)
    assert "العيّنة صغيرة" in verdict(st)


def test_verdict_calls_a_large_positive_gap_worth_watching():
    st = Stats(n=150, wins=80, losses=70, mean_r=0.4, control_n=150, control_mean_r=0.1)
    assert "يتفوّق" in verdict(st)


def test_verdict_calls_a_flat_gap_beta():
    st = Stats(n=150, wins=75, losses=75, mean_r=0.2, control_n=150, control_mean_r=0.19)
    assert "العشوائي" in verdict(st)


def test_verdict_without_a_control_says_so():
    st = Stats(n=150, wins=75, losses=75, mean_r=0.2, control_n=0)
    assert "سيطرة" in verdict(st)


def test_stats_edge_is_none_without_a_control():
    assert Stats(n=1, mean_r=1.0, control_n=0).edge is None


# ── the cards ────────────────────────────────────────────────────────────


def test_the_decision_card_shows_the_trade_and_the_provenance():
    rd = Read(generated_at=1, symbol="XAUUSD", news={})
    card = format_decision(rd, _decision())
    assert "AYZR" in card
    assert "4005" in card and "3990" in card and "4040" in card
    assert "مُتحقّق" in card


def test_the_decision_card_handles_no_model():
    rd = Read(news={})
    d = decide(_facts(), None, error="نفدت الحصة")
    card = format_decision(rd, d)
    assert "تعذّر التحليل" in card


def test_the_decision_card_lists_rejections_when_a_trade_was_thrown_out():
    rd = Read(news={})
    raw = {
        "bias": "bullish",
        "trade": {"direction": "buy", "entry": 4005.0, "stop": 4010.0, "target": 4040.0},
    }
    d = decide(_facts(), raw, model="m", min_rr=2.0)
    card = format_decision(rd, d)
    assert "لا توجد صفقة" in card
    assert "رُفض" in card


def test_the_report_card_leads_with_the_verdict(tmp_path):
    s = _settings(tmp_path)
    card = format_report(build_report(s))
    assert "دفتر" in card
    assert "الحكم" in card


# ── the prompt: structure + news + macro, in one message ─────────────────


def test_build_prompt_includes_structure_levels():
    from goldbot.ayzr.prompt import build_prompt

    text = build_prompt(_facts(), None, 2.0)
    assert "4000" in text and "المخاطرة" not in text  # numbers are present
    assert "fair_value_gaps" in text


def test_build_prompt_includes_news_and_macro_when_read():
    from goldbot.ayzr.prompt import build_prompt

    news = {
        "blackout": {"active": False},
        "upcoming": [{"title": "FOMC"}],
        "headlines": [{"title": "Gold up"}],
        "macro": {"checked": True, "bias": "دعم", "fx_change_1d_pct": -0.3, "real_yield_10y": 1.8},
    }
    text = build_prompt(_facts(), news, 2.0)
    assert "FOMC" in text
    assert "Gold up" in text
    assert "real_yield_10y" in text


def test_build_prompt_omits_an_unread_macro_leg():
    from goldbot.ayzr.prompt import build_prompt

    news = {"blackout": {}, "headlines": [], "macro": {"checked": False}}
    text = build_prompt(_facts(), news, 2.0)
    assert "real_yield_10y" not in text


def test_the_prompt_trims_to_the_nearest_zones():
    from goldbot.ayzr.prompt import _nearest

    zones = [
        {"top": 4010, "bottom": 4005},
        {"top": 4500, "bottom": 4490},
        {"top": 4001, "bottom": 3995},
    ]
    near = _nearest(zones, 4000, 1)
    assert near == [{"top": 4001, "bottom": 3995}]


def test_the_prompt_nearest_handles_a_missing_close():
    from goldbot.ayzr.prompt import _nearest

    zones = [{"top": 1, "bottom": 0}, {"top": 3, "bottom": 2}]
    assert _nearest(zones, None, 2) == zones
    assert _nearest([], 4000, 2) == []


# ── the client: transport, quota parsing, JSON recovery ──────────────────


def _http_error(code: int, body: dict):
    import io
    import urllib.error

    return urllib.error.HTTPError(
        "https://x", code, "err", {}, io.BytesIO(json.dumps(body).encode())
    )


def test_complete_returns_the_model_reply(tmp_path, monkeypatch):
    import urllib.request

    s = _settings(tmp_path, ai_key="k")
    payload = {"choices": [{"message": {"content": '{"bias":"bullish"}'}}]}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    reply, model = AiClient(s).complete("sys", "user")
    assert reply == '{"bias":"bullish"}'
    assert model == s.ai_model


def test_a_per_minute_429_is_retried_then_reported(tmp_path, monkeypatch):
    import urllib.request

    from goldbot.ayzr import client as mod

    monkeypatch.setattr(mod, "RETRY_BACKOFF", 0)
    s = _settings(tmp_path, ai_key="k", ai_model="only", ai_fallbacks=())

    def boom(*a, **k):
        raise _http_error(429, {"error": {"code": "rate_limit_exceeded", "message": "slow down"}})

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(AiBudgetError) as exc:
        AiClient(s).complete("s", "u")
    assert "تجاوز" in str(exc.value)


def test_a_daily_429_parks_the_model(tmp_path, monkeypatch):
    import urllib.request

    s = _settings(tmp_path, ai_key="k", ai_model="only", ai_fallbacks=())
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: (_ for _ in ()).throw(
            _http_error(
                429,
                {
                    "error": {
                        "code": "rate_limit_exceeded",
                        "message": "Rate limit per day exceeded",
                    }
                },
            )
        ),
    )
    c = AiClient(s)
    with pytest.raises(AiBudgetError):
        c.complete("s", "u")
    assert c._parked("only") is True
    with pytest.raises(AiBudgetError) as exc:
        c.complete("s", "u")
    assert "كل موديلات" in str(exc.value)


def test_a_json_validate_failure_moves_on(tmp_path, monkeypatch):
    import urllib.request

    s = _settings(tmp_path, ai_key="k", ai_model="a", ai_fallbacks=("b",))
    calls = {"n": 0}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"ok":1}'}}]}).encode()

    def route(req, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(400, {"error": {"code": "json_validate_failed", "message": "bad"}})
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", route)
    reply, model = AiClient(s).complete("s", "u")
    assert model == "b"


def test_empty_content_is_an_error(tmp_path, monkeypatch):
    import urllib.request

    s = _settings(tmp_path, ai_key="k", ai_model="a", ai_fallbacks=())

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {"content": ""}}]}).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    from goldbot.ayzr import client as mod

    monkeypatch.setattr(mod, "RETRY_BACKOFF", 0)
    with pytest.raises(AiBudgetError) as exc:
        AiClient(s).complete("s", "u")
    assert (
        "empty" in str(exc.value) or "فارغ" in str(exc.value) or "empty content" in str(exc.value)
    )


def test_parse_retry_seconds_reads_groq_wording():
    from goldbot.ayzr.client import _parse_retry_seconds

    exc = _http_error(429, {"error": {"message": "Please try again in 12.5s"}})
    assert _parse_retry_seconds(exc) == 12.5


# ── the read: through a fixed feed ───────────────────────────────────────


def _feed(bars_by_tf):
    class Feed:
        def series(self, tf, bars):
            tape = bars_by_tf.get(tf)
            if tape is None:

                class R:
                    attempts = 1

                return None, R()
            return Series(timeframe=tf, bars=tape, source="test"), None

        def spot_check(self, series):
            return {"price": series.bars[-1].close, "trusted": True, "max_divergence_pct": 0.1}

    return Feed()


def test_read_market_builds_facts_and_context(tmp_path, monkeypatch):
    from goldbot.ayzr.read import read_market

    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {"checked": True})
    s = _settings(tmp_path)
    tape = _bars(1_700_000_000, 80, step=1.0)
    rd = read_market(s, feed=_feed({"M15": tape}))
    assert rd.ok
    assert rd.spot and rd.spot["price"] > 0
    assert rd.series["M15"]["bars"] == 80


def test_read_market_reports_when_no_frame_returns(tmp_path, monkeypatch):
    from goldbot.ayzr.read import read_market

    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    s = _settings(tmp_path)
    rd = read_market(s, feed=_feed({}))
    assert not rd.ok
    assert rd.errors


def test_read_market_survives_a_feed_that_raises(tmp_path, monkeypatch):
    from goldbot.ayzr.read import read_market

    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})

    class Boom:
        def series(self, tf, bars):
            raise RuntimeError("feed down")

    s = _settings(tmp_path)
    rd = read_market(s, feed=Boom())
    assert not rd.ok
    assert any("feed down" in e for e in rd.errors)


def test_readonly_feed_has_no_archive(tmp_path):
    from goldbot.ayzr.read import _build_readonly_feed

    s = _settings(tmp_path)
    feed = _build_readonly_feed(s)
    assert feed.archive is None


# ── the engine end to end, with a fake client ────────────────────────────


def test_engine_run_journals_a_validated_trade(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="k")
    # An oscillating tape has real swing levels, so the proposed entry can sit
    # on one — the validator accepts a level the model actually pointed at.
    import math

    tape = [
        Bar(
            time=1_700_000_000 + i * 900,
            open=4000 + math.sin(i / 5) * 20,
            high=4000 + math.sin(i / 5) * 20 + 8,
            low=4000 + math.sin(i / 5) * 20 - 8,
            close=4000 + math.sin(i / 5) * 20,
            volume=1.0,
        )
        for i in range(80)
    ]
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    engine = Engine(s, feed=_feed({"M15": tape}))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def complete(self, sys_, user):
            raw = {
                "bias": "bullish",
                "market_state": "x",
                "confidence": 0.5,
                "scenarios": {"primary": {"thesis": "t"}},
                "trade": {
                    "direction": "buy",
                    "entry": 3972.0,  # the last swing low from the tape
                    "stop": 3952.0,
                    "target": 4032.0,
                },
            }
            return json.dumps(raw), "fake"

    monkeypatch.setattr("goldbot.ayzr.engine.AiClient", FakeClient)
    run = engine.run(with_ai=True)
    assert run.decision.has_trade, run.decision.rejected
    assert run.journalled is not None
    assert read_all(s)


def test_engine_run_records_no_trade(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="k")
    tape = _bars(1_700_000_000, 80, step=1.0)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    engine = Engine(s, feed=_feed({"M15": tape}))

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def complete(self, sys_, user):
            return json.dumps({"trade": {"direction": "none"}, "bias": "neutral"}), "fake"

    monkeypatch.setattr("goldbot.ayzr.engine.AiClient", FakeClient)
    run = engine.run(with_ai=True)
    assert not run.decision.has_trade
    assert read_all(s)[0]["kind"] == "no_trade"


def test_engine_run_without_ai_does_not_journal_a_call(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="")
    tape = _bars(1_700_000_000, 80, step=1.0)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    run = Engine(s, feed=_feed({"M15": tape})).run(with_ai=True)
    assert read_all(s) == []
    assert run.decision.error


def test_engine_run_reports_when_there_is_no_read(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    run = Engine(s, feed=_feed({})).run()
    assert not run.read.ok
    assert run.error


def test_engine_resolve_only(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    now = int(time.time()) - 10_000
    log_signal(s, _decision(), _facts(), price=4005.0)
    rows = read_all(s)
    rows[0]["logged_at"] = now
    s.log_path.write_text(json.dumps(rows[0], ensure_ascii=False) + "\n", encoding="utf-8")
    tape = [
        Bar(time=now + 900 * (i + 1), open=4005, high=4045, low=4000, close=4044, volume=1)
        for i in range(3)
    ]
    assert Engine(s, feed=_feed({"M15": tape})).resolve_only() == 1


# ── the panel: the one seam the bot calls ────────────────────────────────


def test_panel_report_action(tmp_path):
    from goldbot.ayzr.panel import handle

    s = _settings(tmp_path)
    from goldbot.ayzr.engine import Engine

    text = handle("ayzr:report", engine=Engine(s))
    assert "دفتر" in text


def test_panel_resolve_action(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine
    from goldbot.ayzr.panel import handle

    s = _settings(tmp_path)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    text = handle("ayzr:resolve", engine=Engine(s, feed=_feed({"M15": _bars(1, 50)})))
    assert "حُسمت" in text


def test_panel_analyse_action(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine
    from goldbot.ayzr.panel import handle

    s = _settings(tmp_path, ai_key="")
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    text = handle(
        "ayzr:analyse", engine=Engine(s, feed=_feed({"M15": _bars(1_700_000_000, 80, step=1.0)}))
    )
    assert "AYZR" in text


def test_panel_reports_a_dead_read(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine
    from goldbot.ayzr.panel import handle

    s = _settings(tmp_path)
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    text = handle("ayzr:analyse", engine=Engine(s, feed=_feed({})))
    assert "تعذّر" in text


# ── the CLI ──────────────────────────────────────────────────────────────


def test_cli_analyse_json(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    # No network: the feed fails, which is itself a valid JSON output.
    assert cli.main(["analyse", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["read"]["ok"] is False


def test_cli_report(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    assert cli.main(["report"]) == 0
    assert "AYZR" in capsys.readouterr().out


def test_cli_resolve(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    assert cli.main(["resolve"]) == 0
    assert "حُسمت" in capsys.readouterr().out


def test_cli_journal_json(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    assert cli.main(["journal", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []


# ── journal compaction ───────────────────────────────────────────────────


def test_compact_folds_a_resolution_into_its_signal(tmp_path):
    from goldbot.ayzr.journal import compact

    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    rows = read_all(s)
    key = f"{rows[0]['logged_at']}:{rows[0]['direction']}"
    s.log_path.write_text(
        json.dumps(rows[0], ensure_ascii=False)
        + "\n"
        + json.dumps(
            {
                "kind": "resolution",
                "key": key,
                "outcome": "win",
                "r_multiple": 2.3,
                "bars_held": 4,
                "control": {"direction": "sell", "outcome": "loss", "r_multiple": -1.0},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    compact(s)
    folded = read_all(s)
    assert len(folded) == 1
    assert folded[0]["outcome"] == "win"
    assert folded[0]["control"]["outcome"] == "loss"


def test_compact_keeps_no_trade_records(tmp_path):
    from goldbot.ayzr.journal import compact

    s = _settings(tmp_path)
    d = decide(_facts(), {"trade": {"direction": "none"}, "bias": "x"}, model="m")
    log_no_trade(s, d, price=1.0)
    compact(s)
    assert read_all(s)[0]["kind"] == "no_trade"


# ── report extras ────────────────────────────────────────────────────────


def test_report_counts_no_trade_calls(tmp_path):
    from goldbot.ayzr.report import no_trade_count

    s = _settings(tmp_path)
    d = decide(_facts(), {"trade": {"direction": "none"}, "bias": "x"}, model="m")
    log_no_trade(s, d, price=1.0)
    assert no_trade_count(s) == 1
    assert no_trade_count(s, since=int(time.time()) + 100) == 0


def test_compute_respects_the_since_filter(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    rows = read_all(s)
    key = f"{rows[0]['logged_at']}:{rows[0]['direction']}"
    with open(s.log_path, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "kind": "resolution",
                    "key": key,
                    "outcome": "win",
                    "r_multiple": 2.0,
                    "resolved_at": 100,
                    "control": {"outcome": "loss", "r_multiple": -1.0, "direction": "sell"},
                }
            )
            + "\n"
        )
    assert compute(s, since=200).n == 0
    assert compute(s, since=0).n == 1


def test_read_all_skips_a_corrupt_line(tmp_path):
    s = _settings(tmp_path)
    s.log_path.write_text("{bad json}\n" + json.dumps({"kind": "x"}) + "\n", encoding="utf-8")
    assert len(read_all(s)) == 1


def test_report_formats_a_populated_stats_block(tmp_path):
    from goldbot.ayzr.report import Report

    rep = Report(today=Stats(n=3, wins=2, losses=1, mean_r=0.4, control_n=3, control_mean_r=0.1))
    card = format_report(rep)
    assert "نسبة النجاح" in card


# ── card branches: every field that can be present is rendered ───────────


def test_the_decision_card_renders_every_optional_field():
    rd = Read(
        news={
            "macro": {
                "checked": True,
                "bias": "دعم",
                "fx_change_1d_pct": -0.2,
                "real_yield_10y": 1.7,
            }
        }
    )
    raw = {
        "market_state": "ضغط صاعد",
        "bias": "bullish",
        "technical": "CHoCH",
        "fundamental": "دولار ضعيف",
        "reasoning": "سبب مفصّل",
        "confidence": 0.5,
        "scenarios": {
            "primary": {"thesis": "عودة", "trigger": "إغلاق فوق 4010", "invalidation": "كسر 3990"}
        },
        "trade": {
            "direction": "buy",
            "entry": 4005.0,
            "stop": 3990.0,
            "target": 4040.0,
            "zone": "M15 FVG",
            "why": "سبب",
        },
    }
    card = format_decision(rd, decide(_facts(), raw, model="m", min_rr=2.0))
    for token in (
        "حالة السوق",
        "الفني",
        "الاقتصادي",
        "الانحياز",
        "يؤكّده",
        "يُبطله",
        "المنطقة",
        "السبب",
        "الشرح",
        "ثقة",
        "عائد حقيقي",
    ):
        assert token in card, token


def test_the_macro_line_reports_when_unread():
    rd = Read(news={})
    assert "تعذّر الفحص" in format_decision(
        rd, decide(_facts(), {"trade": {"direction": "none"}}, model="m")
    )


def test_the_macro_line_with_no_parts_says_unavailable():
    from goldbot.ayzr.cards import _macro_line

    assert "غير متاح" in _macro_line(Read(news={"macro": {"checked": True}}))


def test_the_report_block_shows_a_control_when_present():
    from goldbot.ayzr.report import Report

    st = Stats(n=2, wins=1, losses=1, total_r=0.5, mean_r=0.25, control_n=2, control_mean_r=0.1)
    card = format_report(Report(today=st, total=st))
    assert "العشوائي" in card


# ── failure paths that must degrade, never crash ─────────────────────────


def test_settings_tolerate_bad_numbers(monkeypatch):
    from goldbot.ayzr.config import AyzrSettings

    monkeypatch.setenv("AYZR_BARS", "not-a-number")
    monkeypatch.setenv("AYZR_MIN_RR", "nope")
    s = AyzrSettings()
    assert s.bars == 400
    assert s.min_rr == 2.0


def test_settings_data_root_prefers_its_own_env(monkeypatch, tmp_path):
    from goldbot.ayzr.config import AyzrSettings

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "own"))
    assert AyzrSettings().data_dir == tmp_path / "own"


def test_read_all_survives_an_unreadable_file(tmp_path):
    s = _settings(tmp_path)
    s.log_path.mkdir()  # a directory where a file is expected
    assert read_all(s) == []


def test_resolve_open_is_a_noop_without_bars(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)

    class Feed:
        def series(self, tf, bars):
            return None, None

    assert resolve_open(s, Feed()) == 0


def test_read_market_reports_a_spot_failure(tmp_path, monkeypatch):
    from goldbot.ayzr.read import read_market

    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})

    class Feed:
        def series(self, tf, bars):
            return Series(timeframe=tf, bars=_bars(1_700_000_000, 80, step=1.0), source="t"), None

        def spot_check(self, series):
            raise RuntimeError("spot down")

    rd = read_market(_settings(tmp_path), feed=Feed())
    assert rd.ok
    assert any("spot" in e for e in rd.errors)


def test_read_market_reports_a_news_failure(tmp_path, monkeypatch):
    from goldbot.ayzr.read import read_market

    def boom(now, with_macro):
        raise RuntimeError("news down")

    monkeypatch.setattr("goldbot.ayzr.read.news_digest", boom)
    rd = read_market(_settings(tmp_path), feed=_feed({"M15": _bars(1_700_000_000, 80)}))
    assert any("news" in e for e in rd.errors)


def test_engine_survives_a_resolve_failure(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path)

    class Feed:
        def series(self, tf, bars):
            raise RuntimeError("feed down")

    run = Engine(s, feed=Feed()).run()
    assert run.error


def test_engine_reports_an_unreachable_model(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="k")
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def complete(self, sys_, user):
            raise RuntimeError("boom")

    monkeypatch.setattr("goldbot.ayzr.engine.AiClient", FakeClient)
    run = Engine(s, feed=_feed({"M15": _bars(1_700_000_000, 80)})).run(with_ai=True)
    assert "تعذّر" in run.decision.error


def test_engine_reports_a_non_json_reply(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="k")
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def complete(self, sys_, user):
            return "not json", "m"

    monkeypatch.setattr("goldbot.ayzr.engine.AiClient", FakeClient)
    run = Engine(s, feed=_feed({"M15": _bars(1_700_000_000, 80)})).run(with_ai=True)
    assert "JSON" in run.decision.error


def test_engine_reports_a_budget_error(tmp_path, monkeypatch):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path, ai_key="k")
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def complete(self, sys_, user):
            raise AiBudgetError("نفدت الحصة")

    monkeypatch.setattr("goldbot.ayzr.engine.AiClient", FakeClient)
    run = Engine(s, feed=_feed({"M15": _bars(1_700_000_000, 80)})).run(with_ai=True)
    assert "نفدت" in run.decision.error


def test_engine_report_and_price_helpers(tmp_path):
    from goldbot.ayzr.engine import Engine, Run

    s = _settings(tmp_path)
    e = Engine(s)
    assert e.report().total.n == 0
    r = Run()
    assert r.price is None


def test_engine_builds_its_own_feed_when_none_given(tmp_path):
    from goldbot.ayzr.engine import Engine

    s = _settings(tmp_path)
    feed = Engine(s)._feed_or_build()
    assert feed is not None


def test_client_reports_a_failed_second_attempt(tmp_path, monkeypatch):
    import urllib.request

    from goldbot.ayzr import client as mod

    monkeypatch.setattr(mod, "RETRY_BACKOFF", 0)
    s = _settings(tmp_path, ai_key="k", ai_model="a", ai_fallbacks=())

    def boom(*a, **k):
        raise OSError("dns down")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(AiBudgetError) as exc:
        AiClient(s).complete("s", "u")
    assert "dns" in str(exc.value)


def test_client_reports_an_empty_choice_list(tmp_path, monkeypatch):
    import urllib.request

    from goldbot.ayzr import client as mod

    monkeypatch.setattr(mod, "RETRY_BACKOFF", 0)
    s = _settings(tmp_path, ai_key="k", ai_model="a", ai_fallbacks=())

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"choices": []}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    with pytest.raises(AiBudgetError):
        AiClient(s).complete("s", "u")


def test_client_parks_reason_when_nothing_is_parked(tmp_path):
    s = _settings(tmp_path, ai_key="k")
    assert "0" in AiClient(s)._parked_reason()


def test_http_reason_reads_the_body_once(tmp_path):
    from goldbot.ayzr.client import _http_reason

    exc = _http_error(
        429, {"error": {"code": "rate_limit_exceeded", "message": "first line\nsecond"}}
    )
    first = _http_reason(exc)
    second = _http_reason(exc)  # the cached body, not a drained stream
    assert first == second


def test_cli_analyse_prints_a_card(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    monkeypatch.setattr("goldbot.ayzr.read.news_digest", lambda now, with_macro: {})
    assert cli.main(["analyse", "--no-ai"]) == 0
    assert "AYZR" in capsys.readouterr().out


def test_cli_journal_prints_a_count(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    assert cli.main(["journal"]) == 0
    assert "0" in capsys.readouterr().out


def test_compute_counts_timeouts_and_no_fills(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    rows = read_all(s)
    key = f"{rows[0]['logged_at']}:{rows[0]['direction']}"
    with open(s.log_path, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "kind": "resolution",
                    "key": key,
                    "outcome": "timeout",
                    "r_multiple": 0.0,
                    "control": {"outcome": "no_fill", "r_multiple": None, "direction": "sell"},
                }
            )
            + "\n"
        )
    st = compute(s)
    assert st.timeouts == 1
    assert st.edge is None  # the control never filled


def test_resolve_leaves_a_fresh_signal_open(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    now = int(time.time())

    class Feed:
        def series(self, tf, bars):
            tape = [
                Bar(time=now + 900 * (i + 1), open=4100, high=4101, low=4099, close=4100, volume=1)
                for i in range(2)
            ]
            return Series(timeframe=tf, bars=tape, source="t"), None

    assert resolve_open(s, Feed()) == 0


# ── remaining decision branches ──────────────────────────────────────────


def test_a_zero_risk_trade_is_rejected():
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 4005.0, "stop": 4005.0, "target": 4040.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("صفرية" in r for r in reasons)


def test_a_sell_with_a_wrong_stop_is_rejected():
    trade, reasons = validate_trade(
        {"direction": "sell", "entry": 4005.0, "stop": 3990.0, "target": 3970.0}, _facts(), 2.0
    )
    assert trade == {}
    assert any("ترتيب" in r for r in reasons)


def test_the_entry_can_sit_in_an_order_block():
    facts = _facts()
    trade, reasons = validate_trade(
        {"direction": "buy", "entry": 3990.0, "stop": 3980.0, "target": 4020.0}, facts, 2.0
    )
    assert reasons == [] and trade["validated"]


def test_a_zone_with_a_missing_bound_is_skipped():
    from goldbot.ayzr.decide import _bounds

    assert _bounds({"top": 1}) == (None, None)
    assert _bounds({"bottom": "x", "top": "y"}) == (None, None)
    assert _bounds({"bottom": 2, "top": 1}) == (1.0, 2.0)


def test_the_primary_frame_falls_back_when_m15_is_absent():
    from goldbot.ayzr.decide import _primary_frame

    facts = {"timeframes": {"H4": {"last_close": 1.0}}}
    assert _primary_frame(facts) == {"last_close": 1.0}
    assert _primary_frame({"timeframes": {}}) == {}


# ── remaining report branches ────────────────────────────────────────────


def test_compute_counts_a_no_fill(tmp_path):
    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    rows = read_all(s)
    key = f"{rows[0]['logged_at']}:{rows[0]['direction']}"
    with open(s.log_path, "a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "kind": "resolution",
                    "key": key,
                    "outcome": "no_fill",
                    "r_multiple": None,
                    "control": {"outcome": "win", "r_multiple": 2.0, "direction": "sell"},
                }
            )
            + "\n"
        )
    st = compute(s)
    assert st.no_fill == 1
    assert st.total_r == 0.0


def test_verdict_flags_a_losing_edge():
    st = Stats(n=150, wins=50, losses=100, mean_r=-0.2, control_n=150, control_mean_r=0.1)
    assert "أسوأ" in verdict(st)


def test_open_count_counts_only_unresolved(tmp_path):
    from goldbot.ayzr.report import open_count

    s = _settings(tmp_path)
    log_signal(s, _decision(), _facts(), price=1.0)
    assert open_count(s) == 1


def test_cli_report_json(tmp_path, monkeypatch, capsys):
    from goldbot.ayzr import cli

    monkeypatch.setenv("AYZR_DATA_DIR", str(tmp_path / "ayzr"))
    assert cli.main(["report", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["total"]["n"] == 0
