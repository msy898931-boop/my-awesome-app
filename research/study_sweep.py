"""Study 1: does a liquidity sweep carry a measurable edge?

The backtest only produced four sweep-reversal trades — far too few to judge
anything. This ignores the plan builder and measures the *raw* event: find
every sweep, then ask what price did next.

Three things make the measurement honest, and all three matter:

1. **Causality.** Swings need two bars after them to confirm, so a sweep is
   read only at the bar where it closes and the outcome is measured from the
   next bar on.

2. **A local control.** A global drift figure is worthless here: the window
   trends, so it flatters whichever side matches the trend, and the drift is
   not even stationary (a 60-bar drift of -4.2 ATR on this sample is absurd
   when the whole 721-bar window moved 1.5%). Each sweep is instead compared
   with the average move of the bars *around it* — same local regime, no
   trend credit. The edge is the paired difference.

3. **A stratified read.** The live setup is not "any sweep": it is a sweep of
   the lows inside a discount, in a market whose higher frames are bullish.
   So the numbers are split by trend agreement and by dealing-range zone,
   because the aggregate can hide the only cell that matters.

Run:  PYTHONPATH=src python3 research/study_sweep.py
"""

from __future__ import annotations

import statistics
import sys

sys.path.insert(0, "src")

from goldbot.config import load_settings
from goldbot.data.feed import build_feed
from goldbot.smc.primitives import atr, swings

HORIZON = 60
CONTROL_SPAN = 150  # bars either side used as the local control


def find_sweeps(bars, lookback: int = 2, max_age: int = 60):
    """Every sweep as ``(index, side, level)``.

    A sweep is a bar that takes a prior swing's extreme and closes back on the
    other side of it — the stop-hunt, not the break.

    Two constraints keep this honest. The level must be the **most recent**
    swing, because the nearest pool is the one that gets run; accepting any
    swing within ``max_age`` flags 44% of all bars, which is not a sweep
    detector, it is a wick detector. And ``max_age`` refuses a level too stale
    to be the pool that was just taken.
    """
    highs, lows = swings(bars, lookback)
    out = []
    for i, bar in enumerate(bars):
        recent_high = None
        for idx, price in highs:
            if idx < i <= idx + max_age:
                recent_high = price
            elif idx >= i:
                break
        if recent_high is not None and bar.high > recent_high and bar.close < recent_high:
            out.append((i, "buy_side_taken", recent_high))

        recent_low = None
        for idx, price in lows:
            if idx < i <= idx + max_age:
                recent_low = price
            elif idx >= i:
                break
        if recent_low is not None and bar.low < recent_low and bar.close > recent_low:
            out.append((i, "sell_side_taken", recent_low))
    return out


def forward_move(bars, i, horizon=HORIZON):
    future = bars[i + 1 : i + 1 + horizon]
    if not future:
        return None
    return future[-1].close - bars[i].close


def local_control(bars, i, horizon=HORIZON, span=CONTROL_SPAN):
    """Mean forward move of the bars around ``i``, excluding the sweep itself.

    This is the drift the market was giving away in that neighbourhood. Using
    the whole series instead would let a week-long slide masquerade as skill.
    """
    lo = max(0, i - span)
    hi = min(len(bars) - horizon - 1, i + span)
    moves = [
        bars[j + horizon].close - bars[j].close
        for j in range(lo, hi)
        if abs(j - i) > 5 and j + horizon < len(bars)
    ]
    if not moves:
        return None
    return statistics.mean(moves)


def sign_of_move(side):
    """A sweep of the lows argues up (buy); a sweep of the highs argues down."""
    return 1 if side == "sell_side_taken" else -1


def zone_at(bars, i):
    """Premium/discount of the 220-bar range ending at ``i``, as a fraction."""
    window = bars[max(0, i - 219) : i + 1]
    if len(window) < 20:
        return None
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    if hi <= lo:
        return None
    return (bars[i].close - lo) / (hi - lo)


def trend_at(bars, i, span=200):
    """A single causal trend read: close against its own trailing mean."""
    if i < span:
        return None
    window = bars[i - span + 1 : i + 1]
    mean = sum(b.close for b in window) / len(window)
    if mean == 0:
        return None
    gap = (bars[i].close - mean) / mean
    if gap > 0.0005:
        return "bullish"
    if gap < -0.0005:
        return "bearish"
    return "range"


def describe(rows, label):
    """Report the paired edge: sweep move minus its local control."""
    if not rows:
        print(f"  {label:34s}: لا عيّنة")
        return
    edges = [e for e, _ in rows]
    agree = sum(1 for _, a in rows if a)
    print(
        f"  {label:34s}: n={len(edges):3d} | "
        f"حافة={statistics.mean(edges):+.3f} ATR | "
        f"وسيط={statistics.median(edges):+.3f} | "
        f"موجب={sum(1 for e in edges if e > 0) / len(edges) * 100:.0f}% | "
        f"مع الاتجاه={agree / len(edges) * 100:.0f}%"
    )


def collect(settings, timeframe):
    """Run the sweep read on one timeframe and return its paired rows."""
    feed = build_feed(settings)
    series, report = feed.series(timeframe, 5000)
    if series is None or len(series.bars) < 150:
        return None, None, None
    bars = series.bars
    atr_value = atr(bars)
    if not atr_value:
        return None, None, None
    rows = []
    for i, side, _ in find_sweeps(bars):
        fwd = forward_move(bars, i)
        ctrl = local_control(bars, i)
        if fwd is None or ctrl is None:
            continue
        d = sign_of_move(side)
        rows.append({
            "tf": timeframe, "i": i, "side": side, "atr": atr_value,
            "edge": d * (fwd - ctrl) / atr_value,
            "zone": zone_at(bars, i), "trend": trend_at(bars, i),
        })
    return series, rows, bars


def matched_baseline(bars, atr_value, sweep_indices, zone_ok, direction):
    """The same measurement on bars that satisfy the zone but carry no sweep.

    This is the test that decides the whole study. The cell above compares
    sweeps in premium against *everything*, which credits the zone and the
    drift as if they were the sweep's doing. Comparing against non-sweep bars
    in the same zone asks the only question that matters: does the sweep
    itself add information on top of standing in premium?
    """
    vals = []
    for i in range(120, len(bars) - HORIZON - 1):
        if i in sweep_indices:
            continue
        z = zone_at(bars, i)
        if not zone_ok(z):
            continue
        fwd = forward_move(bars, i)
        ctrl = local_control(bars, i)
        if fwd is None or ctrl is None:
            continue
        vals.append(direction * (fwd - ctrl) / atr_value)
    return vals


def main():
    settings = load_settings()
    pooled: list[dict] = []
    print("== كل فريم على حدة ==")
    for tf in ("H4", "H1", "M30", "M15"):
        series, rows, _ = collect(settings, tf)
        if series is None:
            print(f"  {tf:4s}: لا بيانات كافية")
            continue
        n = len(series.bars)
        if rows:
            edges = [r["edge"] for r in rows]
            print(f"  {tf:4s}: {n:4d} شمعة | كنسات={len(rows):3d} | "
                  f"حافة={statistics.mean(edges):+.3f} ATR | "
                  f"موجب={sum(1 for e in edges if e > 0) / len(edges) * 100:.0f}%")
        else:
            print(f"  {tf:4s}: {n:4d} شمعة | كنسات=0")
        pooled.extend(rows)

    if not pooled:
        print("\nلا عيّنة مجمّعة.")
        return

    print(f"\n== المجموع المُجمَّع عبر الفريمات: {len(pooled)} كنسة ≈ 4 أشهر ==")
    describe([(r["edge"], r["edge"] > 0) for r in pooled], "كل الكنسات")
    for side in ("sell_side_taken", "buy_side_taken"):
        sub = [r for r in pooled if r["side"] == side]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"  {side}")

    print("\n== حسب الاتجاه العام ==")
    for side in ("sell_side_taken", "buy_side_taken"):
        want = "bullish" if side == "sell_side_taken" else "bearish"
        sub = [r for r in pooled if r["side"] == side and r["trend"] == want]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"{side} مع الاتجاه")
        sub = [r for r in pooled if r["side"] == side and r["trend"] not in (want, None)]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"{side} ضد الاتجاه")

    print("\n== حسب المنطقة ==")
    for side in ("sell_side_taken", "buy_side_taken"):
        good = (lambda z: z is not None and z < 0.38) if side == "sell_side_taken" \
            else (lambda z: z is not None and z > 0.62)
        sub = [r for r in pooled if r["side"] == side and good(r["zone"])]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"{side} في منطقته")
        sub = [r for r in pooled if r["side"] == side and not good(r["zone"])]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"{side} خارج منطقته")

    print("\n== الخلية التي يتداولها البوت فعلاً: كنس + اتجاه + منطقة ==")
    for side in ("sell_side_taken", "buy_side_taken"):
        want = "bullish" if side == "sell_side_taken" else "bearish"
        good = (lambda z: z is not None and z < 0.38) if side == "sell_side_taken" \
            else (lambda z: z is not None and z > 0.62)
        sub = [r for r in pooled if r["side"] == side and r["trend"] == want and good(r["zone"])]
        describe([(r["edge"], r["edge"] > 0) for r in sub], f"{side} كامل الشروط")

    print("\n== التماثل: يجب أن يتقارب الطرفان ==")
    for side in ("sell_side_taken", "buy_side_taken"):
        sub = [r["edge"] for r in pooled if r["side"] == side]
        if sub:
            print(f"  {side:17s}: n={len(sub):3d} | حافة={statistics.mean(sub):+.3f} | "
                  f"وسيط={statistics.median(sub):+.3f} | "
                  f"موجب={sum(1 for e in sub if e > 0) / len(sub) * 100:.0f}%")

    print("\n== ثبات الإشارة عبر الفريمات (هل نفس الاتجاه في كل فريم؟) ==")
    for side in ("sell_side_taken", "buy_side_taken"):
        parts = []
        for tf in ("H4", "H1", "M30", "M15"):
            sub = [r["edge"] for r in pooled if r["side"] == side and r["tf"] == tf]
            parts.append(f"{tf}={statistics.mean(sub):+.2f}(n={len(sub)})" if sub else f"{tf}=—")
        print(f"  {side:17s}: " + " | ".join(parts))

    print("\n== الخلية الواعدة وحدها عبر الفريمات ==")
    print("   كنس القمم داخل premium (فرضية البيع). يجب أن تثبت في كل فريم.")
    for tf in ("H4", "H1", "M30", "M15"):
        sub = [r for r in pooled
               if r["tf"] == tf and r["side"] == "buy_side_taken"
               and r["zone"] is not None and r["zone"] > 0.62]
        if sub:
            e = [r["edge"] for r in sub]
            print(f"  {tf:4s}: n={len(e):3d} | حافة={statistics.mean(e):+.3f} | "
                  f"وسيط={statistics.median(e):+.3f} | موجب={sum(1 for x in e if x > 0) / len(e) * 100:.0f}%")
        else:
            print(f"  {tf:4s}: لا عيّنة")

    print("\n== عتبة المنطقة: أين ينقلب السلوك؟ (كنس القمم) ==")
    for thr in (0.5, 0.55, 0.6, 0.62, 0.7, 0.8):
        sub = [r["edge"] for r in pooled
               if r["side"] == "buy_side_taken" and r["zone"] is not None and r["zone"] > thr]
        if sub:
            print(f"  premium > {thr}: n={len(sub):3d} | حافة={statistics.mean(sub):+.3f} | "
                  f"موجب={sum(1 for x in sub if x > 0) / len(sub) * 100:.0f}%")

    print("\n== عتبة المنطقة: كنس القيعان ==")
    for thr in (0.5, 0.45, 0.4, 0.38, 0.3, 0.2):
        sub = [r["edge"] for r in pooled
               if r["side"] == "sell_side_taken" and r["zone"] is not None and r["zone"] < thr]
        if sub:
            print(f"  discount < {thr}: n={len(sub):3d} | حافة={statistics.mean(sub):+.3f} | "
                  f"موجب={sum(1 for x in sub if x > 0) / len(sub) * 100:.0f}%")

    print("\n== تحذير التكرار: نفس الحدث يظهر في عدة فريمات ==")
    print("   H4 و M15 يتقاطعان زمنياً، فالملاحظات ليست مستقلة والعدد متضخّم.")

    print("\n== الاختبار الحاسم: هل الكنسة تضيف فوق شرط المنطقة؟ ==")
    print("   المقارنة مع شمعات بلا كنسة في نفس المنطقة، بنفس الطريقة.")
    for tf in ("H4", "H1", "M30", "M15"):
        feed = build_feed(settings)
        series, _, bars = collect(settings, tf)
        if series is None:
            continue
        atr_value = atr(bars)
        idx = {i for i, _, _ in find_sweeps(bars)}
        sweep_edges = [r["edge"] for r in pooled
                       if r["tf"] == tf and r["side"] == "buy_side_taken"
                       and r["zone"] is not None and r["zone"] > 0.62]
        base = matched_baseline(bars, atr_value, idx,
                                lambda z: z is not None and z > 0.62, -1)
        s = f"{statistics.mean(sweep_edges):+.3f}(n={len(sweep_edges)})" if sweep_edges else "—"
        b = f"{statistics.mean(base):+.3f}(n={len(base)})" if base else "—"
        lift = (statistics.mean(sweep_edges) - statistics.mean(base)) if sweep_edges and base else None
        lift_s = f"{lift:+.3f}" if lift is not None else "—"
        print(f"  {tf:4s}: كنسة={s} | بلا كنسة={b} | الفرق={lift_s}")

    print("\n== نفس الاختبار لكن داخل discount، لكشف المرآة ==")
    for tf in ("H4", "H1", "M30", "M15"):
        feed = build_feed(settings)
        series, _, bars = collect(settings, tf)
        if series is None:
            continue
        atr_value = atr(bars)
        idx = {i for i, _, _ in find_sweeps(bars)}
        sweep_edges = [r["edge"] for r in pooled
                       if r["tf"] == tf and r["side"] == "sell_side_taken"
                       and r["zone"] is not None and r["zone"] < 0.38]
        base = matched_baseline(bars, atr_value, idx,
                                lambda z: z is not None and z < 0.38, +1)
        s = f"{statistics.mean(sweep_edges):+.3f}(n={len(sweep_edges)})" if sweep_edges else "—"
        b = f"{statistics.mean(base):+.3f}(n={len(base)})" if base else "—"
        lift = (statistics.mean(sweep_edges) - statistics.mean(base)) if sweep_edges and base else None
        lift_s = f"{lift:+.3f}" if lift is not None else "—"
        print(f"  {tf:4s}: كنسة={s} | بلا كنسة={b} | الفرق={lift_s}")


if __name__ == "__main__":
    main()
