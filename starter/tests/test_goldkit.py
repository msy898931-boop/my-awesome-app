"""Tests for goldkit — the honesty-first plumbing.

The tests that matter are the refusals: a failure must not look quiet, a retry
must actually back off, and a stale value must not be served as clean. Each of
these was a bug in GoldBot before it was a pattern here, so they are pinned.
"""

from __future__ import annotations

import urllib.error

import pytest

from goldkit import (
    ALLOWED_SCHEMES,
    Leg,
    ResilientSource,
    Result,
    UnsafeURL,
    guarded_url,
    read_capped,
)
from goldkit.http import MAX_BODY_BYTES, ResponseTooLarge

# ── Result ──────────────────────────────────────────────────────────────


def test_result_truthiness_tracks_health_not_content():
    # An empty-but-healthy read is truthy; that is the whole point — "no data"
    # and "no feed" must not be conflated.
    assert Result.good([])
    assert not Result.failed("boom", [])


def test_a_failed_result_keeps_the_error_and_marks_a_kept_value_stale():
    r = Result.failed("HTTP 429", ["old"])
    assert r.ok is False and r.error == "HTTP 429"
    assert r.stale is True and r.value == ["old"]
    # Nothing to keep → failed but not stale.
    assert Result.failed("HTTP 429", []).stale is False


def test_the_guard_reads_the_scheme_of_a_prepared_request():
    """A ``Request`` object is accepted, and its scheme is checked the same way."""
    import urllib.request

    with pytest.raises(UnsafeURL):
        guarded_url(urllib.request.Request("file:///etc/passwd"), timeout=1)


def test_result_as_dict_carries_the_flags():
    d = Result.failed("HTTP 500", {"a": 1}).as_dict()
    assert d["ok"] is False and d["error"] == "HTTP 500" and d["stale"] is True


def test_result_as_dict_merges_meta():
    d = Result.good({"a": 1}, checked=True, source="fred").as_dict()
    assert d["ok"] is True and d["checked"] is True and d["source"] == "fred"


# ── Leg: the retry gate ─────────────────────────────────────────────────


def _leg(responses):
    """A Leg whose fetch pops from ``responses``: ``(value, error)`` tuples."""
    calls = {"n": 0}

    def fetch():
        item = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return item

    return Leg(fetch=fetch, ttl=100.0, empty=[]), calls


def test_a_good_read_is_cached_within_its_ttl():
    leg, calls = _leg([(["v"], "")])
    assert leg.value(now=1000.0) == ["v"]
    assert calls["n"] == 1
    leg.value(now=1050.0)  # inside the 100s TTL
    assert calls["n"] == 1


def test_a_failure_backs_off_instead_of_hammering():
    """Bug 1: the gate must be an absolute deadline, or it never fires."""
    leg, calls = _leg([([], "HTTP 429")])
    leg.value(now=1000.0)
    assert calls["n"] == 1
    # Well inside NEGATIVE_TTL (300s): no second attempt.
    leg.value(now=1010.0)
    leg.value(now=1100.0)
    assert calls["n"] == 1
    # Past the gate: it tries again.
    leg.value(now=1400.0)
    assert calls["n"] == 2


def test_a_stale_value_is_never_served_as_clean():
    """Bug 2: freshness needs ``not error`` as well as a value within the TTL."""
    leg, _ = _leg([(["v"], ""), (["v"], "HTTP 429")])
    assert leg.value(now=1000.0) == ["v"]
    assert leg.ok is True

    # A failure keeps the value but records the error.
    leg.value(now=1100.0)  # past the TTL → refetch, which fails
    assert leg.value(now=1200.0) == ["v"]  # value kept
    assert leg.ok is False and leg.error == "HTTP 429"
    assert leg.stale is True

    # Even though the value is present and would be "within TTL" of the last
    # success, it must NOT be reported clean on the next call.
    leg.value(now=1300.0)
    assert leg.ok is False
    assert leg.error == "HTTP 429"


def test_a_leg_recovers_when_the_source_comes_back():
    leg, _ = _leg([([], "HTTP 429"), (["v"], "")])
    leg.value(now=1000.0)
    assert leg.ok is False
    leg.value(now=1400.0)  # past the gate, source answers
    assert leg.ok is True and leg.error == "" and leg.value(now=1400.0) == ["v"]


def test_snapshot_reports_the_three_flags():
    leg, _ = _leg([([], "HTTP 500")])
    leg.value(now=1000.0)
    snap = leg.snapshot()
    assert snap == {"ok": False, "error": "HTTP 500", "stale": False}


# ── ResilientSource ─────────────────────────────────────────────────────


def test_a_source_is_ok_only_when_every_leg_answers():
    src = ResilientSource(name="mixed")
    src.add("good", lambda: ({"a": 1}, ""), ttl=100.0)
    src.add("bad", lambda: ({}, "HTTP 429"), ttl=100.0)

    out = src.read(now=1000.0)
    assert out.ok is False
    assert "bad: HTTP 429" in out.error
    # The good leg's value is still there — a partial read is useful, and honest.
    assert out.value["good"] == {"a": 1}
    assert out.stale is True


def test_a_source_with_every_leg_down_is_failed_not_stale():
    src = ResilientSource(name="dead")
    src.add("a", lambda: ({}, "HTTP 500"), ttl=100.0)
    src.add("b", lambda: ([], "HTTP 500"), ttl=100.0)

    out = src.read(now=1000.0)
    assert out.ok is False and out.stale is False
    assert "a: HTTP 500" in out.error and "b: HTTP 500" in out.error


def test_a_healthy_source_reports_ok_and_checked():
    src = ResilientSource(name="fine")
    src.add("a", lambda: ({"x": 1}, ""), ttl=100.0)
    out = src.read(now=1000.0)
    assert out.ok is True and out.value["a"] == {"x": 1}
    assert out.meta.get("checked") is True


def test_source_health_lists_each_leg():
    src = ResilientSource(name="s")
    src.add("a", lambda: (1, ""), ttl=100.0)
    src.add("b", lambda: (2, ""), ttl=100.0)
    src.read(now=1000.0)
    h = src.health()
    assert h["ok"] is True
    assert set(h["legs"]) == {"a", "b"}


# ── http: the guarded door and the cap ──────────────────────────────────


def test_guarded_url_refuses_a_non_web_scheme():
    for bad in ("file:///etc/passwd", "ftp://host/x", "data:text/plain,x", "javascript:1"):
        with pytest.raises(UnsafeURL):
            guarded_url(bad, timeout=1)


def test_guarded_url_allows_http_and_https():
    assert "http" in ALLOWED_SCHEMES and "https" in ALLOWED_SCHEMES
    assert "file" not in ALLOWED_SCHEMES


def test_read_capped_accepts_a_body_at_the_limit():
    class _Resp:
        def read(self, n):
            return b"x" * min(n, 5)

    assert read_capped(_Resp(), limit=5) == b"xxxxx"


def test_read_capped_refuses_a_body_over_the_limit():
    class _Resp:
        def read(self, n):
            return b"x" * n  # always returns what was asked

    with pytest.raises(ResponseTooLarge):
        read_capped(_Resp(), limit=10)


def test_the_default_cap_is_bounded():
    assert 0 < MAX_BODY_BYTES <= 10_000_000


def test_the_guard_does_not_swallow_network_errors(monkeypatch):
    """The guard checks the scheme; it must let a real network error through so
    the caller's retry logic can see it."""

    def _boom(target, timeout):
        raise urllib.error.HTTPError("u", 429, "Too Many", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    with pytest.raises(urllib.error.HTTPError):
        guarded_url("https://example.test/x", timeout=1)
