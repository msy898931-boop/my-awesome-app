"""The equity-curve renderer: a chart, or an honest reason it is absent.

Pure functions of a number list, so the tests are pure too — no network, no
files. The point of each is a claim the chart must not break: an empty curve is
named rather than drawn, the zero line appears only when the curve crosses it,
and the same curve always renders the same way.
"""

from __future__ import annotations

import re

from goldbot.backtest.chart import render_curve, render_svg, render_time_curve

# A row whose label is exactly the zero tag, right-aligned before the frame.
_ZERO_ROW = re.compile(r"(?m)^\s*0 │")


def _ramp(n: int = 40) -> list[float]:
    return [round(i * 0.5 - 4.0, 3) for i in range(n)]


def test_an_empty_curve_is_named_not_drawn():
    text = render_curve([])
    assert "لا صفقات" in text
    assert "│" not in text  # no frame, no trace


def test_a_crossing_curve_draws_the_zero_line():
    assert _ZERO_ROW.search(render_curve(_ramp()))


def test_a_curve_that_never_crosses_zero_has_no_zero_line():
    # A top label like "+30.0" must not be mistaken for the zero tag.
    assert not _ZERO_ROW.search(render_curve([1.0 + i for i in range(30)]))


def test_a_flat_curve_does_not_divide_by_zero():
    text = render_curve([2.0] * 25)
    assert "منحنى رأس المال" in text
    assert "•" in text


def test_a_single_point_still_renders():
    assert "1 صفقة" in render_curve([3.5])


def test_tiny_dimensions_are_refused_by_name():
    assert "صغيرة" in render_curve(_ramp(), width=4, height=2)


def test_the_render_is_deterministic():
    assert render_curve(_ramp()) == render_curve(_ramp())


def test_the_header_counts_filled_trades():
    assert "40 صفقة" in render_curve(_ramp(40))


def test_a_title_names_the_curve_so_several_can_be_told_apart():
    text = render_curve(_ramp(), title="استمرار BOS")
    assert "استمرار BOS" in text


def test_a_steep_jump_draws_the_vertical_connector():
    # A big move inside one column: the trace must connect with │, not a gap.
    curve = [-50.0, -50.0, 50.0, 50.0]
    assert "│" in render_curve(curve, width=16, height=10)


def test_svg_is_standalone_and_has_no_script():
    svg = render_svg(_ramp())
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    assert "<polyline" in svg
    assert "<script" not in svg


def test_svg_of_an_empty_curve_says_so():
    svg = render_svg([])
    assert "No filled trades" in svg
    assert "<polyline" not in svg


def test_svg_zero_line_only_when_crossing():
    assert "stroke-dasharray" in render_svg(_ramp())
    assert "stroke-dasharray" not in render_svg([1.0 + i for i in range(30)])


# ── the time axis ──────────────────────────────────────────────────────────
_DAY = 86400


def _pts(values, start=1_700_000_000, step=_DAY):
    return [(start + i * step, v) for i, v in enumerate(values)]


def test_a_time_curve_is_named_and_dated_at_both_ends():
    text = render_time_curve(_pts([1.0, 2.0, 3.0]))
    assert "منحنى رأس المال" in text
    # The span is legible: the first and last dates appear.
    assert "2023-11-14" in text and "2023-11-16" in text


def test_an_empty_time_curve_is_named_not_drawn():
    text = render_time_curve([])
    assert "لا صفقات" in text and "│" not in text


def test_a_burst_of_trades_is_visibly_narrower_than_a_slow_climb():
    """Two curves with the same final R must differ when time is the axis.

    One rises over a year, the other over three days. Against a rank axis they
    are identical; against time the fast one's rise is squeezed into far fewer
    columns, so the rendered grids cannot match.
    """
    slow = _pts([1.0, 2.0, 3.0], step=180 * _DAY)
    fast = _pts([1.0, 2.0, 3.0], step=_DAY)
    assert render_time_curve(slow) != render_time_curve(fast)


def test_a_gap_with_no_trades_is_a_flat_run_not_a_steep_jump():
    # Flat at 2 for half a year, then one jump: the flat stretch must dominate.
    pts = _pts([2.0, 2.0], step=180 * _DAY) + [(2.0 + 180 * _DAY, 5.0)]
    text = render_time_curve(pts)
    assert "منحنى رأس المال" in text


def test_the_time_curve_is_deterministic():
    assert render_time_curve(_pts([1.0, 2.0])) == render_time_curve(_pts([1.0, 2.0]))


def test_svg_with_points_dates_the_axis():
    svg = render_svg([1.0, 2.0, 3.0], points=_pts([1.0, 2.0, 3.0]))
    assert "2023-11-14" in svg and "2023-11-16" in svg
    assert "<polyline" in svg


def test_svg_without_points_keeps_the_rank_captions():
    svg = render_svg([1.0, 2.0, 3.0])
    assert "first trade" in svg and "last trade (3)" in svg
