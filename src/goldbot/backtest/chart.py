"""Draw the equity curve without a plotting dependency.

The project is standard-library only, and a chart is worth no dependency: the
work is arithmetic and string assembly, so it lives here. Two renderers share
one idea — map a cumulative-R curve onto a grid, mark the zero line, and connect
each column to the next so the trace reads as a path rather than a scatter:

* :func:`render_curve` draws it as text for a terminal.
* :func:`render_svg` draws the same shape as a standalone SVG a browser opens.

Both are pure functions of the curve: no file is written and nothing is fetched.
A curve that is empty or flat is named, never drawn as a misleading line.
"""

from __future__ import annotations

from collections.abc import Sequence

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

    lo, hi = _bounds(curve)
    grid = [[" "] * width for _ in range(height)]

    def col_value(x: int) -> float:
        # Spread the curve across the columns; a shorter curve than columns
        # repeats its last value, so the line ends where the data ends.
        if len(curve) == 1:
            return curve[0]
        pos = x * (len(curve) - 1) / (width - 1)
        left = int(pos)
        right = min(left + 1, len(curve) - 1)
        frac = pos - left
        return curve[left] * (1 - frac) + curve[right] * frac

    prev: int | None = None
    for x in range(width):
        row = _row(col_value(x), lo, hi, height)
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

    name_w = max(len(f"{hi:+.1f}"), len(f"{lo:+.1f}"), 4)
    name = f" — {title}" if title else ""
    lines = [f"  منحنى رأس المال{name} (بالـ {label}) — {len(curve)} صفقة"]
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
    return "\n".join(lines)


def render_svg(
    curve: Sequence[float],
    width: int = 720,
    height: int = 320,
    title: str = "Equity curve (R)",
) -> str:
    """A standalone SVG of the same curve; empty input draws an honest note.

    Kept deliberately plain — a polyline, a zero baseline, four labels — so it
    needs no script, no font beyond the system default, and no stylesheet.
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

    def px(i: int) -> float:
        return pad_l + (i * plot_w / (n - 1) if n > 1 else plot_w / 2)

    def py(v: float) -> float:
        return pad_t + (hi - v) / (hi - lo) * plot_h

    points = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(curve))
    zero = ""
    if lo <= 0 <= hi:
        y = py(0.0)
        zero = (
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + plot_w}" y2="{y:.1f}" '
            'stroke="#bbb" stroke-dasharray="4 4" stroke-width="1"/>'
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" '
            'font-family="sans-serif" font-size="11" fill="#888">0</text>'
        )

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
        f'points="{points}"/>\n'
        f'  <text x="{pad_l}" y="{height - 8}" font-size="11" fill="#888">'
        f"first trade</text>\n"
        f'  <text x="{pad_l + plot_w}" y="{height - 8}" text-anchor="end" '
        f'font-size="11" fill="#888">last trade ({n})</text>\n'
        "</svg>\n"
    )
