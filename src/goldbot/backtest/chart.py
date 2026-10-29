"""Draw the equity curve without a plotting dependency.

The project is standard-library only, and a chart is worth no dependency: the
work is arithmetic and string assembly, so it lives here. The renderers share
one idea — map a curve onto a grid, mark the zero line, and connect each column
to the next so the trace reads as a path rather than a scatter:

* :func:`render_curve` draws a cumulative-R curve against *trade rank* as text.
* :func:`render_time_curve` draws the same curve against *time* as text, so a
  run-up that took three months and a collapse that took three days stop looking
  identical.
* :func:`render_svg` draws a curve as a standalone SVG a browser opens.

All are pure functions of their input: no file is written and nothing is
fetched. A curve that is empty or flat is named, never drawn as a misleading
line.
"""

from __future__ import annotations

import bisect
import time as _time
from collections.abc import Callable, Sequence

# The trace and the frame. Chosen from the box-drawing block so the line joins
# cleanly on any terminal that renders the report's own rules.
_TRACE = "•"
_VERT = "│"
_FLAT = "─"


def _bounds(curve: Sequence[float]) -> tuple[float, float]:
    lo, hi = min(curve), max(curve)
    # A flat curve has no range to scale by; give it one so it sits mid-grid
    # rather than dividing by zero.
    if hi == lo:
        return lo - 1.0, hi + 1.0
    return lo, hi


def _row(value: float, lo: float, hi: float, height: int) -> int:
    """The grid row for a value, 0 at the top, ``height - 1`` at the bottom."""
    frac = (value - lo) / (hi - lo)
    return height - 1 - round(frac * (height - 1))


def _grid(
    sample: Callable[[int], float],
    values: Sequence[float],
    width: int,
    height: int,
) -> tuple[list[list[str]], float, float, int | None]:
    """Rasterise ``sample`` across ``width`` columns onto a ``height``-row grid.

    ``sample(x)`` gives the curve's value at column ``x``; ``values`` supplies
    the vertical range. Returns the grid, its lo/hi bounds, and the zero row
    (or ``None`` when the range does not bracket break-even).
    """
    lo, hi = _bounds(values)
    grid = [[" "] * width for _ in range(height)]

    prev: int | None = None
    for x in range(width):
        row = _row(sample(x), lo, hi, height)
        grid[row][x] = _TRACE
        if prev is not None and prev != row:
            step = 1 if row > prev else -1
            for r in range(prev + step, row, step):
                grid[r][x] = _VERT
        prev = row

    # The zero line: only drawn when the range brackets it, so its absence
    # itself says the study never crossed break-even.
    zero_row = _row(0.0, lo, hi, height) if lo <= 0 <= hi else None
    if zero_row is not None:
        for x in range(width):
            if grid[zero_row][x] == " ":
                grid[zero_row][x] = _FLAT
    return grid, lo, hi, zero_row


def _frame(
    grid: list[list[str]],
    lo: float,
    hi: float,
    zero_row: int | None,
    header: str,
    left_cap: str,
    right_cap: str,
) -> str:
    """Wrap a grid in its axis labels and frame, then join the lines."""
    name_w = max(len(f"{hi:+.1f}"), len(f"{lo:+.1f}"), 4, len("0"))
    height = len(grid)
    width = len(grid[0])
    lines = [header]
    for r in range(height):
        tag = ""
        if r == 0:
            tag = f"{hi:+.1f}"
        elif r == height - 1:
            tag = f"{lo:+.1f}"
        elif zero_row is not None and r == zero_row:
            tag = "0"
        lines.append(f"  {tag:>{name_w}} │{''.join(grid[r])}")
    lines.append(f"  {'':>{name_w}} └{'─' * width}")
    # The x-axis caption sits under the frame, aligned to its two ends.
    gap = max(1, width - len(left_cap) - len(right_cap))
    lines.append(f"  {'':>{name_w}}  {left_cap}{' ' * gap}{right_cap}")
    return "\n".join(lines)


def _rank_sampler(curve: Sequence[float], width: int) -> Callable[[int], float]:
    """Value at each column when the x-axis is trade rank."""

    def sample(x: int) -> float:
        # Spread the curve across the columns; a shorter curve than columns
        # repeats its last value, so the line ends where the data ends.
        if len(curve) == 1:
            return curve[0]
        pos = x * (len(curve) - 1) / (width - 1)
        left = int(pos)
        right = min(left + 1, len(curve) - 1)
        frac = pos - left
        return curve[left] * (1 - frac) + curve[right] * frac

    return sample


def render_curve(
    curve: Sequence[float],
    width: int = 64,
    height: int = 12,
    label: str = "R",
    title: str | None = None,
) -> str:
    """The cumulative-R curve as a text chart, or a named reason it is absent.

    ``curve`` is :func:`goldbot.backtest.report.equity_curve`'s output: one
    running total per filled trade. The columns span the whole curve, so a long
    study is compressed rather than truncated, and the zero line is drawn
    whenever the range crosses it — the single most useful reference on an
    equity curve, since it marks the break-even the strategy must clear.
    ``title`` names the curve (a setup, say) so several can be told apart.
    """
    if not curve:
        return "منحنى رأس المال: لا صفقات منفّذة لرسمها."
    if width < 8 or height < 3:
        return "منحنى رأس المال: أبعاد الرسم صغيرة جدًا."

    grid, lo, hi, zero_row = _grid(_rank_sampler(curve, width), curve, width, height)
    name = f" — {title}" if title else ""
    header = f"  منحنى رأس المال{name} (بالـ {label}) — {len(curve)} صفقة"
    return _frame(grid, lo, hi, zero_row, header, "أول صفقة", f"آخر صفقة ({len(curve)})")


def _day(ts: int) -> str:
    return _time.strftime("%Y-%m-%d", _time.gmtime(ts))


def render_time_curve(
    points: Sequence[tuple[int, float]],
    width: int = 64,
    height: int = 12,
    label: str = "R",
    title: str | None = None,
) -> str:
    """The cumulative-R curve as text with an x-axis of *dates*, not rank.

    ``points`` is :func:`goldbot.backtest.report.equity_points`'s output:
    ``(unix time, cumulative R)`` after each filled trade. Columns are spaced by
    elapsed time, so a quiet stretch with no trades is a flat run at one height
    and a burst of trades is a steep segment — the reading a rank axis cannot
    give. The ends are dated so the span is legible.
    """
    if not points:
        return "منحنى رأس المال: لا صفقات منفّذة لرسمها."
    if width < 8 or height < 3:
        return "منحنى رأس المال: أبعاد الرسم صغيرة جدًا."

    times = [p[0] for p in points]
    values = [p[1] for p in points]
    t0, t1 = times[0], times[-1]
    span = t1 - t0

    def sample(x: int) -> float:
        # Binary-search the column's time, then interpolate between its
        # neighbours. Times are sorted, so this is O(log n) per column.
        if span <= 0 or len(points) == 1:
            return values[-1]
        target = t0 + span * x / (width - 1)
        i = bisect.bisect_left(times, target)
        if i <= 0:
            return values[0]
        if i >= len(times):
            return values[-1]
        lo_t, hi_t = times[i - 1], times[i]
        frac = 0.0 if hi_t == lo_t else (target - lo_t) / (hi_t - lo_t)
        return values[i - 1] * (1 - frac) + values[i] * frac

    grid, lo, hi, zero_row = _grid(sample, values, width, height)
    name = f" — {title}" if title else ""
    header = f"  منحنى رأس المال{name} (بالـ {label}) — {len(values)} صفقة"
    return _frame(grid, lo, hi, zero_row, header, _day(t0), _day(t1))


def render_svg(
    curve: Sequence[float],
    width: int = 720,
    height: int = 320,
    title: str = "Equity curve (R)",
    points: Sequence[tuple[int, float]] | None = None,
) -> str:
    """A standalone SVG of a cumulative-R curve; empty input draws an honest note.

    Kept deliberately plain — a polyline, a zero baseline, four labels — so it
    needs no script, no font beyond the system default, and no stylesheet. When
    ``points`` (``(time, R)``) is given the x-axis is spaced by time and the
    ends are dated; otherwise the curve is spaced by trade rank.
    """
    pad_l, pad_r, pad_t, pad_b = 56, 16, 36, 28
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    if not curve:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
            f'height="{height}"><text x="{width / 2}" y="{height / 2}" '
            f'text-anchor="middle" font-family="sans-serif" font-size="14">'
            "No filled trades to plot</text></svg>"
        )

    lo, hi = _bounds(curve)
    n = len(curve)
    dated = points is not None and len(points) == n and n > 1
    stamps: Sequence[tuple[int, float]] = points if dated and points is not None else []

    def px(i: int) -> float:
        if dated:
            t0, t1 = stamps[0][0], stamps[-1][0]
            frac = (stamps[i][0] - t0) / (t1 - t0) if t1 > t0 else i / (n - 1)
        else:
            frac = i / (n - 1) if n > 1 else 0.5
        return pad_l + frac * plot_w

    def py(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * plot_h

    points_attr = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(curve))
    zero = ""
    if lo <= 0 <= hi:
        y = py(0.0)
        zero = (
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            'stroke="#bbb" stroke-dasharray="4 4" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" '
            'font-family="sans-serif" font-size="11" fill="#888">0</text>'
        )

    if dated:
        left_cap, right_cap = _day(stamps[0][0]), _day(stamps[-1][0])
    else:
        left_cap, right_cap = "first trade", f"last trade ({n})"

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        'font-family="sans-serif">\n'
        f'  <rect width="{width}" height="{height}" fill="#fff"/>\n'
        f'  <text x="{pad_l}" y="20" font-size="14" fill="#333">{title}</text>\n'
        f'  <text x="{pad_l - 6}" y="{pad_t + 4}" text-anchor="end" font-size="11" '
        f'fill="#888">{hi:+.1f}</text>\n'
        f'  <text x="{pad_l - 6}" y="{pad_t + plot_h:.1f}" text-anchor="end" '
        f'font-size="11" fill="#888">{lo:+.1f}</text>\n'
        f"  {zero}\n"
        f'  <polyline fill="none" stroke="#1f6feb" stroke-width="1.5" '
        f'points="{points_attr}"/>\n'
        f'  <text x="{pad_l}" y="{height - 8}" font-size="11" fill="#888">'
        f"{left_cap}</text>\n"
        f'  <text x="{pad_l + plot_w}" y="{height - 8}" text-anchor="end" '
        f'font-size="11" fill="#888">{right_cap}</text>\n'
        "</svg>\n"
    )
