"""Professional metrics for a backtest, and the report that presents them.

The engine resolves trades; this module judges them. A single total-R figure is
not a verdict — a strategy that made +20R in one lucky trade and bled the rest
is not the same as one that ground out +20R in two hundred. So every metric
here is chosen to answer a question a trader actually asks:

* **Expectancy** (mean R) — what one trade is worth on average. The headline.
* **Profit factor** — gross win over gross loss. Reads a tail without hiding it.
* **Payoff ratio** — what a win is worth against a loss. Small wins and one big
  loss look identical in a win rate; they do not here.
* **Max drawdown, in R** — the worst peak-to-trough of the equity curve. The
  number that decides whether a real account survives.
* **Longest losing streak** — how many in a row. Drawdown in trades, not R.
* **Sharpe / Sortino, per trade** — return per unit of total and of *downside*
  risk. Reported without an annualisation factor on purpose: the number of
  trades per year depends on the frame, and silently annualising by a guessed
  frequency would invent precision the sample does not have.
* **Consistency** — the share of out-of-sample windows that were positive. A
  strategy carried by one window is a regime, and this is where it shows.

Everything is a pure function of a trade list, so it is deterministic, offline,
and testable without a feed. The one rule that matters: a metric that cannot be
computed is reported as ``None`` and printed as "—", never as zero. Zero is a
measurement; ``None`` is the absence of one, and the two must not look alike.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from .engine import Trade

# A window with fewer fills than this cannot support a per-window metric; it is
# reported as thin rather than as a zero that would drag a consistency score.
MIN_WINDOW_TRADES = 5


def _filled(trades: Sequence[Trade]) -> list[Trade]:
    """Real trades only. An unfilled limit order is not a result."""
    return [t for t in trades if t.outcome != "no_fill"]


def metrics(trades: Sequence[Trade]) -> dict:
    """Every headline number for one set of trades, honest about emptiness."""
    filled = _filled(trades)
    out: dict = {
        "n": len(filled),
        "no_fill": len(trades) - len(filled),
        "wins": None,
        "losses": None,
        "timeouts": None,
        "win_rate": None,
        "expectancy": None,
        "profit_factor": None,
        "payoff": None,
        "total_r": None,
        "avg_win_r": None,
        "avg_loss_r": None,
        "max_drawdown_r": None,
        "longest_loss_streak": None,
        "sharpe": None,
        "sortino": None,
        "best_r": None,
        "worst_r": None,
    }
    if not filled:
        return out

    rs = [t.r_multiple for t in filled]
    # A "win" means the target was reached — the same binary the walk engine
    # reports — so a timeout that merely closed at a positive mark is not a win.
    # Two layers that call the same number by the same name must not disagree.
    won = [t for t in filled if t.outcome == "win"]
    lost = [t for t in filled if t.outcome == "loss"]
    decided = len(won) + len(lost)

    out["wins"] = len(won)
    out["losses"] = len(lost)
    out["timeouts"] = len(filled) - len(won) - len(lost)
    out["win_rate"] = round(len(won) / decided, 4) if decided else None
    out["expectancy"] = round(statistics.mean(rs), 4)
    out["total_r"] = round(sum(rs), 2)
    # Profit factor is money over every filled trade, so its partitions are the
    # sign of R, not the outcome: a timeout that closed up is gross profit and
    # one that closed down is gross loss, or the factor would not match the curve.
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))
    out["profit_factor"] = round(gross_win / gross_loss, 3) if gross_loss else None
    out["avg_win_r"] = round(statistics.mean([t.r_multiple for t in won]), 3) if won else None
    out["avg_loss_r"] = round(statistics.mean([t.r_multiple for t in lost]), 3) if lost else None
    if won and lost:
        out["payoff"] = round(out["avg_win_r"] / abs(out["avg_loss_r"]), 3)

    out["max_drawdown_r"] = _max_drawdown(rs)
    out["longest_loss_streak"] = _longest_loss_streak(rs)
    out["sharpe"] = _sharpe(rs)
    out["sortino"] = _sortino(rs)
    out["best_r"] = round(max(rs), 3)
    out["worst_r"] = round(min(rs), 3)
    return out


def _max_drawdown(rs: Sequence[float]) -> float:
    """Worst peak-to-trough of the cumulative R curve, as a negative number."""
    peak = 0.0
    cum = 0.0
    worst = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        worst = min(worst, cum - peak)
    return round(worst, 2)


def _longest_loss_streak(rs: Sequence[float]) -> int:
    streak = worst = 0
    for r in rs:
        streak = streak + 1 if r < 0 else 0
        worst = max(worst, streak)
    return worst


def _sharpe(rs: Sequence[float]) -> float | None:
    """Per-trade mean return over its standard deviation. Not annualised."""
    if len(rs) < 2:
        return None
    sd = statistics.pstdev(rs)
    return round(statistics.mean(rs) / sd, 3) if sd else None


def _sortino(rs: Sequence[float]) -> float | None:
    """Per-trade mean over *downside* deviation. Penalises only the losses."""
    if len(rs) < 2:
        return None
    downside = [r for r in rs if r < 0]
    if not downside:
        return None
    dd = statistics.pstdev(downside)
    return round(statistics.mean(rs) / dd, 3) if dd else None


def equity_curve(trades: Sequence[Trade]) -> list[float]:
    """Cumulative R after each filled trade, in the order given."""
    curve = []
    cum = 0.0
    for t in _filled(trades):
        cum += t.r_multiple
        curve.append(round(cum, 3))
    return curve


def equity_points(trades: Sequence[Trade]) -> list[tuple[int, float]]:
    """``(time, cumulative R)`` after each filled trade, oldest first.

    The same curve as :func:`equity_curve`, but carrying the clock. Rank hides
    that trades cluster: twenty fills in one week and twenty across a year look
    identical against a trade counter. Plotting against time is what shows a
    run-up that took three months and a collapse that took three days.
    """
    points = []
    cum = 0.0
    for t in sorted(_filled(trades), key=lambda t: t.time):
        cum += t.r_multiple
        points.append((t.time, round(cum, 3)))
    return points


def windows(trades: Sequence[Trade], count: int) -> list[list[Trade]]:
    """Split filled trades into ``count`` contiguous blocks, oldest first.

    Contiguous, not interleaved: an out-of-sample window means a stretch of
    *time* the strategy was not fitted on, and shuffling trades across windows
    would destroy exactly that property. The block boundaries are by rank so
    the windows carry equal trade counts even when the market was quiet.
    """
    filled = sorted(_filled(trades), key=lambda t: t.time)
    if count <= 1 or not filled:
        return [filled]
    size = len(filled) // count
    if size < 1:
        return [filled]
    out = [filled[i * size : (i + 1) * size] for i in range(count)]
    # The remainder rides on the last window rather than being dropped.
    tail = filled[count * size :]
    if tail:
        out[-1] = out[-1] + tail
    return out


def consistency(blocks: Sequence[Sequence[Trade]]) -> dict:
    """How many out-of-sample windows were positive, over those with enough."""
    judged = [b for b in blocks if len(_filled(b)) >= MIN_WINDOW_TRADES]
    if not judged:
        return {"windows": 0, "positive": 0, "share": None, "thin": len(blocks)}
    positive = sum(1 for b in judged if statistics.mean(t.r_multiple for t in _filled(b)) > 0)
    return {
        "windows": len(judged),
        "positive": positive,
        "share": round(positive / len(judged), 3),
        "thin": len(blocks) - len(judged),
    }


def _fmt(value, digits: int = 3, suffix: str = "", *, signed: bool = True) -> str:
    """A value, or an em dash. ``None`` must never be printed as a number.

    A return-like figure is signed so its direction reads at a glance; a
    proportion (a win rate) is not — a leading ``+`` on 29.5% says nothing.
    """
    if value is None:
        return "—"
    if isinstance(value, float):
        sign = "+" if signed else ""
        return f"{value:{sign}.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _ar_count(n: int, singular: str, dual: str, plural: str) -> str:
    """Arabic counted-noun agreement: dual for two, plural for three to ten."""
    if n == 2:
        return f"2 {dual}"
    if n == 1 or not (3 <= n % 100 <= 10):
        return f"{n} {singular}"
    return f"{n} {plural}"


def format_report(result: dict) -> str:
    """A printable report: sample, headline, per window, and the control verdict."""
    lines: list[str] = []
    m = result["metrics"]
    lines.append("═" * 64)
    lines.append(f"  باكتست احترافي — {result['symbol']} على {result['primary_tf']}")
    lines.append("═" * 64)
    lines.append(
        f"  الشموع: {result['bars']} | الصفقات: {m['n']} (استُثنيت {m['no_fill']} بلا تنفيذ)"
    )
    span = f"{result['span_days']} يوم"
    if result.get("period"):
        span += f" ({result['period'][0]} → {result['period'][1]})"
    lines.append(f"  النافذة الزمنية: {span} | نوافذ: {result['windows']}")
    # The bars and the trades need not cover the same calendar: a long warmup or
    # a gate that opens late can leave half the sample trade-free, and the bar
    # span would then overstate the evidence. Say where the trades actually are.
    tp = result.get("trade_period")
    if tp and result.get("trade_span_days") is not None:
        share = (
            round(100 * result["trade_span_days"] / result["span_days"])
            if result.get("span_days")
            else None
        )
        line = f"  نافذة الصفقات الفعلية: {result['trade_span_days']} يوم ({tp[0]} → {tp[1]})"
        if share is not None and share < 90:
            line += f" — {share}% من نافذة الشموع فقط"
        lines.append(line)
    lines.append("")
    lines.append("  ── المقاييس الرئيسية ──")
    lines.append(f"    التوقّع (Expectancy)     : {_fmt(m['expectancy'])} R للصفقة")
    lines.append(f"    إجمالي R                 : {_fmt(m['total_r'])}")
    lines.append(
        f"    نسبة النجاح              : {_fmt(m['win_rate'] and m['win_rate'] * 100, 1, '%', signed=False)}"
    )
    if m.get("wins") is not None:
        lines.append(
            f"    الحصيلة                  : {m['wins']} ربح · {m['losses']} خسارة · "
            f"{m['timeouts']} مهلة (المهلة تُحتسب في R و PF لا في نسبة النجاح)"
        )
    lines.append(f"    معامل الربح (PF)         : {_fmt(m['profit_factor'], signed=False)}")
    lines.append(f"    متوسط الربح / الخسارة   : {_fmt(m['avg_win_r'])} / {_fmt(m['avg_loss_r'])}")
    lines.append(f"    نسبة العائد (Payoff)     : {_fmt(m['payoff'], signed=False)}")
    lines.append(f"    أقصى تراجع (بالـ R)      : {_fmt(m['max_drawdown_r'])}")
    lines.append(
        f"    أطول سلسلة خسارة         : {_ar_count(m['longest_loss_streak'] or 0, 'صفقة', 'صفقتان', 'صفقات')}"
    )
    lines.append(f"    Sharpe / Sortino         : {_fmt(m['sharpe'])} / {_fmt(m['sortino'])}")
    lines.append(f"    أفضل / أسوأ صفقة         : {_fmt(m['best_r'])} / {_fmt(m['worst_r'])}")

    c = result["consistency"]
    lines.append("")
    lines.append("  ── الثبات عبر الزمن (النوافذ) ──")
    if c["share"] is None:
        lines.append(f"    نوافذ قابلة للحكم: 0 (رقيقة: {c['thin']}) — لا حكم")
    else:
        lines.append(
            f"    نوافذ موجبة: {c['positive']}/{c['windows']} ({c['share'] * 100:.0f}%)"
            + (f" | رقيقة مُستثناة: {c['thin']}" if c["thin"] else "")
        )
        verdict = "متّسق" if c["share"] >= 0.6 else "متذبذب — يعتمد على نافذة"
        lines.append(f"    → {verdict}")

    if result.get("control"):
        out = result["control"]
        lines.append("")
        lines.append("  ── هل هي مهارة أم بيتا؟ (حزمة السيطرة) ──")
        v = out["verdict"]
        lines.append(f"    الحكم: {v}")
        if out["real_mean"] is not None:
            lines.append(
                f"    متوسط R={_fmt(out['real_mean'])} | سيطرة عشوائية={_fmt(out['random_mean'])}"
                f" (p={_fmt(out['random_p'], signed=False)})"
                f" | سيطرة النظام={_fmt(out['regime_mean'])}"
                f" (p={_fmt(out['regime_p'], signed=False)})"
            )
        lines.append(
            "    ملاحظة: skill تعني تفوّقاً على السيطرة العشوائية *و* سيطرة النظام؛ "
            "ما دون ذلك بيتا أو غير حاسم."
        )

    lines.append("")
    lines.append("  ── حسب الاتجاه ──")
    for direction, sub in result["by_direction"].items():
        lines.append(
            f"    {direction:5s}: n={sub['n']:4d} | توقّع={_fmt(sub['expectancy'])}"
            f" | PF={_fmt(sub['profit_factor'], signed=False)}"
        )
    lines.append("")
    lines.append("  ── حسب النموذج ──")
    verdicts = result.get("setup_control") or {}
    for setup, sub in result["by_setup"].items():
        line = (
            f"    {setup[:24]:24s}: n={sub['n']:4d} | توقّع={_fmt(sub['expectancy'])}"
            f" | PF={_fmt(sub['profit_factor'], signed=False)}"
        )
        ctrl = verdicts.get(setup)
        if ctrl and ctrl.get("random_p") is not None:
            line += (
                f" | الحكم={ctrl['verdict']}"
                f" (عشوائي p={_fmt(ctrl['random_p'], signed=False)}"
                f" | نظام p={_fmt(ctrl['regime_p'], signed=False)})"
            )
        lines.append(line)
    if verdicts:
        lines.append(
            "    ملاحظة: «skill» هنا لفريم واحد. حكم لا يُعتدّ به حتى يُثبَت على فريم آخر؛ "
            "BOS على H1 قرأ skill ثم صار beta على H4."
        )
    lines.append("")
    lines.append(f"  ملاحظات: {' | '.join(result['notes'])}")
    lines.append("═" * 64)
    return "\n".join(lines)
