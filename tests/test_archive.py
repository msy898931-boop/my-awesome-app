"""Tests for the accumulating candle archive.

The archive is the only reason a real backtest is possible — free intraday
sources hand back about a week at a time — so its two guarantees are pinned
here: it must never duplicate a bar, and it must never lose the store to a
bad write.
"""

from __future__ import annotations

import json

from conftest import make_bars
from goldbot.data.archive import Archive
from goldbot.models import Bar, Series


def _series(bars, tf="M15"):
    return Series(timeframe=tf, bars=bars, source="test")


def test_merge_then_read_back(tmp_path):
    a = Archive(tmp_path)
    added = a.merge("M15", _series(make_bars([(100, 101, 99, 100), (100, 102, 100, 101)])))
    assert added == 2
    stored = a.series("M15", 10)
    assert stored is not None and len(stored.bars) == 2
    assert stored.source == "archive"


def test_merge_is_idempotent(tmp_path):
    a = Archive(tmp_path)
    bars = make_bars([(100, 101, 99, 100), (100, 102, 100, 101)])
    a.merge("M15", _series(bars))
    added = a.merge("M15", _series(bars))
    assert added == 0, "a repeat of the same bars must add nothing"
    assert len(a.load("M15")) == 2


def test_merge_appends_only_new_times(tmp_path):
    a = Archive(tmp_path)
    a.merge("M15", _series(make_bars([(100, 101, 99, 100), (100, 102, 100, 101)])))
    added = a.merge(
        "M15",
        _series(
            make_bars([(101, 103, 101, 102), (102, 104, 102, 103)], start=1_700_000_000 + 2 * 900)
        ),
    )
    assert added == 2
    assert [b.close for b in a.load("M15")] == [100, 101, 102, 103]


def test_bars_stay_sorted_even_when_given_out_of_order(tmp_path):
    a = Archive(tmp_path)
    late = make_bars([(200, 201, 199, 200)])
    early = make_bars([(100, 101, 99, 100)])
    a.merge("M15", _series(late))
    a.merge("M15", _series(early))
    assert [b.time for b in a.load("M15")] == sorted(b.time for b in a.load("M15"))


def test_newer_revision_wins(tmp_path):
    a = Archive(tmp_path)
    a.merge("M15", _series(make_bars([(100, 101, 99, 100)])))
    # Same open time, revised close: the later fetch must replace it.
    a.merge("M15", _series(make_bars([(100, 101, 99, 100.9)])))
    assert a.load("M15")[0].close == 100.9
    assert len(a.load("M15")) == 1


def test_max_bars_trims_the_oldest(tmp_path):
    a = Archive(tmp_path, max_bars=3)
    a.merge(
        "M15",
        _series(
            make_bars(
                [(100, 101, 99, 100)] * 1
                + [(101, 102, 100, 101), (102, 103, 101, 102), (103, 104, 102, 103)]
            )
        ),
    )
    assert len(a.load("M15")) == 3


def test_corrupt_store_is_treated_as_empty(tmp_path):
    a = Archive(tmp_path)
    a.merge("M15", _series(make_bars([(100, 101, 99, 100)])))
    (tmp_path / "M15.json").write_text("{not json", encoding="utf-8")
    assert a.load("M15") == []


def test_unsane_bars_are_rejected_on_merge(tmp_path):
    a = Archive(tmp_path)
    bad = Bar(time=1, open=100, high=99, low=101, close=100)  # high < low
    a.merge("M15", _series([bad]))
    assert a.load("M15") == []


def test_coverage_reports_the_window(tmp_path):
    a = Archive(tmp_path)
    assert a.coverage("M15")["bars"] == 0
    a.merge("M15", _series(make_bars([(100, 101, 99, 100), (100, 102, 100, 101)])))
    cov = a.coverage("M15")
    assert cov["bars"] == 2 and cov["from"] and cov["to"]


def test_store_is_written_as_json(tmp_path):
    a = Archive(tmp_path)
    a.merge("M15", _series(make_bars([(100, 101, 99, 100)])))
    payload = json.loads((tmp_path / "M15.json").read_text(encoding="utf-8"))
    assert isinstance(payload, list) and payload[0]["close"] == 100.0
