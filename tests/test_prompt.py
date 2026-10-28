"""Tests for the prompt builder — the token budget and the fact sheet trim.

The model may only reason over what this module hands it, and the free tier
meters tokens, so ``_trim_frames`` decides both what the model can cite and
whether the request fits. It was only half covered. No model is called here:
``build_prompt`` is pure string work.
"""

from __future__ import annotations

import json

from goldbot.ai.prompt import SYSTEM_PROMPT, _nearest, _trim_frames, build_prompt


def _frame(**over):
    frame = {
        "trend": "bullish",
        "last_close": 4050.0,
        "atr": 12.0,
        "last_event": {"type": "BOS"},
        "premium_discount": "premium",
        "last_swing_high": 4060.0,
        "last_swing_low": 4040.0,
        "fair_value_gaps": [{"top": 4051.0, "bottom": 4049.0, "filled": False}],
        "order_blocks": [{"top": 4048.0, "bottom": 4046.0, "mitigated": False}],
        "sweeps": [{"side": "high"}, {"side": "low"}],
        "liquidity": {"equal_highs": [4060.0], "equal_lows": [4040.0]},
    }
    frame.update(over)
    return frame


def _facts(**over):
    facts = {"symbol": "XAUUSD", "confluence": {"bias": "bullish"}, "timeframes": {"H1": _frame()}}
    facts.update(over)
    return facts


def _payload(prompt: str) -> dict:
    """The JSON line inside the prompt, parsed back for assertions."""
    line = next(line for line in prompt.splitlines() if line.startswith("{"))
    return json.loads(line)


# ── the system prompt ───────────────────────────────────────────────────


def test_the_system_prompt_forbids_inventing_levels():
    assert "لا تخترع" in SYSTEM_PROMPT


def test_the_system_prompt_demands_a_no_trade_answer_when_unfounded():
    assert "لا توجد صفقة الآن" in SYSTEM_PROMPT


def test_the_system_prompt_asks_for_probability_not_certainty():
    assert "سيرتفع حتماً" in SYSTEM_PROMPT  # named as forbidden
    assert "يُرجّح" in SYSTEM_PROMPT


# ── build_prompt ────────────────────────────────────────────────────────


def test_the_prompt_carries_the_symbol_and_confluence():
    payload = _payload(build_prompt(_facts()))
    assert payload["symbol"] == "XAUUSD"
    assert payload["confluence"] == {"bias": "bullish"}


def test_the_prompt_closes_with_the_json_instruction():
    assert "JSON صالحاً" in build_prompt(_facts())


def test_a_compact_prompt_trims_the_frames():
    payload = _payload(build_prompt(_facts(), compact=True))
    # A field that is not in the keep-list must not survive the trim.
    assert "fair_value_gaps" in payload["timeframes"]["H1"]


def test_a_full_prompt_keeps_the_frames_verbatim():
    facts = _facts()
    facts["timeframes"]["H1"]["some_extra_field"] = "kept"
    payload = _payload(build_prompt(facts, compact=False))
    assert payload["timeframes"]["H1"]["some_extra_field"] == "kept"


def test_no_news_leaves_the_news_key_out():
    payload = _payload(build_prompt(_facts(), news=None))
    assert "news" not in payload


def test_news_is_summarised_and_capped():
    news = {
        "blackout": {"active": True},
        "upcoming": [{"t": i} for i in range(10)],
        "released": [{"t": i} for i in range(10)],
        "headlines": [{"title": f"h{i}"} for i in range(10)],
    }
    payload = _payload(build_prompt(_facts(), news=news))
    assert payload["news"]["blackout"] == {"active": True}
    assert len(payload["news"]["upcoming"]) == 4
    assert len(payload["news"]["released"]) == 3
    assert payload["news"]["headlines"] == ["h0", "h1", "h2", "h3"]


def test_the_prompt_is_valid_json_after_the_arabic_lead_in():
    text = build_prompt(_facts())
    # The Arabic preamble precedes the JSON; the JSON line itself must parse.
    assert _payload(text)["symbol"] == "XAUUSD"


# ── _trim_frames ────────────────────────────────────────────────────────


def test_the_trim_keeps_the_citable_levels():
    out = _trim_frames({"H1": _frame()})
    for key in ("trend", "last_close", "atr", "last_event", "premium_discount"):
        assert key in out["H1"], key


def test_the_trim_drops_a_field_that_is_not_citable():
    out = _trim_frames({"H1": _frame(noise="x")})
    assert "noise" not in out["H1"]


def test_filled_gaps_and_mitigated_blocks_are_removed():
    frame = _frame(
        fair_value_gaps=[
            {"top": 4051.0, "bottom": 4049.0, "filled": True},
            {"top": 4053.0, "bottom": 4051.5, "filled": False},
        ],
        order_blocks=[
            {"top": 4048.0, "bottom": 4046.0, "mitigated": True},
            {"top": 4045.0, "bottom": 4043.0, "mitigated": False},
        ],
    )
    out = _trim_frames({"H1": frame})["H1"]
    assert [g["top"] for g in out["fair_value_gaps"]] == [4053.0]
    assert [o["top"] for o in out["order_blocks"]] == [4045.0]


def test_only_the_two_nearest_zones_survive():
    frame = _frame(
        last_close=4050.0,
        fair_value_gaps=[
            {"top": 4051.0, "bottom": 4050.0},  # mid 4050.5 -> nearest
            {"top": 4053.0, "bottom": 4052.0},  # mid 4052.5
            {"top": 4090.0, "bottom": 4089.0},  # far
        ],
    )
    out = _trim_frames({"H1": frame})["H1"]
    assert len(out["fair_value_gaps"]) == 2


def test_only_the_last_two_sweeps_survive():
    frame = _frame(sweeps=[{"i": 0}, {"i": 1}, {"i": 2}, {"i": 3}])
    out = _trim_frames({"H1": frame})["H1"]
    assert out["sweeps"] == [{"i": 2}, {"i": 3}]


def test_liquidity_is_capped_to_two_each_side():
    frame = _frame(liquidity={"equal_highs": [1, 2, 3, 4], "equal_lows": [5, 6, 7]})
    out = _trim_frames({"H1": frame})["H1"]
    assert out["liquidity"]["equal_highs"] == [1, 2]
    assert out["liquidity"]["equal_lows"] == [5, 6]


def test_missing_lists_do_not_break_the_trim():
    frame = {"trend": "bearish", "last_close": 4000.0}
    out = _trim_frames({"H1": frame})["H1"]
    assert out["fair_value_gaps"] == []
    assert out["order_blocks"] == []
    assert out["liquidity"] == {"equal_highs": [], "equal_lows": []}


def test_every_frame_is_trimmed_independently():
    out = _trim_frames({"H1": _frame(), "H4": _frame(trend="bearish")})
    assert out["H1"]["trend"] == "bullish"
    assert out["H4"]["trend"] == "bearish"


# ── _nearest ────────────────────────────────────────────────────────────


def test_nearest_returns_nothing_for_no_zones():
    assert _nearest([], 100.0, 2) == []


def test_nearest_without_a_numeric_close_takes_the_head():
    zones = [{"top": 1}, {"top": 2}, {"top": 3}]
    assert _nearest(zones, None, 2) == [{"top": 1}, {"top": 2}]


def test_nearest_sorts_by_distance_to_the_midpoint():
    zones = [
        {"top": 10.0, "bottom": 8.0},  # mid 9
        {"top": 102.0, "bottom": 100.0},  # mid 101
        {"top": 21.0, "bottom": 19.0},  # mid 20
    ]
    picked = _nearest(zones, 20.0, 1)
    assert picked == [{"top": 21.0, "bottom": 19.0}]
