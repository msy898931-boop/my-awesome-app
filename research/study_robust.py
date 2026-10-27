"""Study 2: is the strategy's result real, on a sample big enough to say?

The earlier work ran on a 7-day window and could not distinguish an edge from
luck. With the paging Binance feed the sample is now years long, so the
question changes from "what is the number" to "is the number meaningful".

Four checks, because a positive total on its own proves nothing:

1. **Bootstrap confidence interval** on mean R. If the interval straddles zero,
   the strategy is indistinguishable from breakeven no matter how large the
   total looks.
2. **Split-half by time.** An edge that lives in one half only is a regime, not
   an edge.
3. **Per setup and per direction**, to see whether a headline number is carried
   by one cell that will not repeat.
4. **Cost sensitivity.** The backtest is frictionless. Gold spreads and
   slippage are real, so the break-even cost per trade is reported: how much
   room there is before the edge disappears.

Run:  PYTHONPATH=src python3 research/study_robust.py
"""

from __future__ import annotations

import random
import statistics
import sys

sys.path.insert(0, "src")

from goldbot.config import load_settings
from goldbot.backtest.engine import _walk
from goldbot.data.feed import build_feed

BOOTSTRAP = 5000
SEED = 7


def load(settings):
    """Read the study's data from the archive only, never the network.

    A study that fetches live data returns a different answer on every run —
    new bars arrive, the sample moves, and no result can be reproduced. The
    archive is already a de-duplicated merge of every provider ever seen, so
    reading it alone gives the longest series available *and* an identical
    sample on every run.
    """
    from goldbot.data.archive import Archive
    from goldbot.data.feed import MarketFeed

    feed = MarketFeed([], archive=Archive(settings.archive_dir))
    series = {}
    for tf in ("M15", "M30", "H1", "H4"):
        s, _ = feed.series(tf, 20000, prefer_depth=True)
        if s is not None and len(s) >= 90:
            series[tf] = s
    return series


def bootstrap_ci(values, n=BOOTSTRAP, seed=SEED, level=0.95):
    """Percentile bootstrap CI for the mean. Distribution-free, no assumptions."""
    if len(values) < 10:
        return None, None
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(statistics.mean(sample))
    means.sort()
    tail = (1 - level) / 2
    return means[int(tail * n)], means[int((1 - tail) * n)]


def profit_factor(rs):
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))
    return gross_win / gross_loss if gross_loss else float("inf")


def report(fills, label):
    if len(fills) < 10:
        print(f"  {label:22s}: عيّنة صغيرة (n={len(fills)})")
        return
    rs = [t.r_multiple for t in fills]
    wins = sum(1 for r in rs if r > 0)
    lo, hi = bootstrap_ci(rs)
    sig = "مهم" if (lo is not None and (lo > 0 or hi < 0)) else "غير مهم (يشمل الصفر)"
    print(
        f"  {label:22s}: n={len(rs):5d} | WR={wins / len(rs) * 100:5.1f}% | "
        f"avgR={statistics.mean(rs):+.3f} | totR={sum(rs):+8.1f} | "
        f"PF={profit_factor(rs):.2f} | 95%CI=[{lo:+.3f},{hi:+.3f}] → {sig}"
    )


def main():
    settings = load_settings()
    series = load(settings)
    print("عمق البيانات:")
    for tf, s in series.items():
        print(f"  {tf}: {len(s.bars)} شمعة")
    print()

    results = {}
    for prim in ("M15", "M30", "H1"):
        if prim not in series:
            continue
        trades = _walk(series, settings.min_rr, primary_tf=prim)
        fills = [t for t in trades if t.outcome != "no_fill"]
        results[prim] = fills

    print("== النتيجة الكلية + فترة الثقة ==")
    for prim, fills in results.items():
        report(fills, prim)

    print("\n== حسب الاتجاه ==")
    for prim, fills in results.items():
        print(f"-- {prim} --")
        for d in ("buy", "sell"):
            report([t for t in fills if t.direction == d], f"  {d}")

    print("\n== حسب النموذج ==")
    for prim, fills in results.items():
        print(f"-- {prim} --")
        setups = sorted({t.setup for t in fills})
        for s in setups:
            report([t for t in fills if t.setup == s], f"  {s[:20]}")

    print("\n== الثبات: النصف الأول مقابل الثاني (حسب الزمن) ==")
    for prim, fills in results.items():
        if not fills:
            continue
        ordered = sorted(fills, key=lambda t: t.time)
        mid = len(ordered) // 2
        print(f"-- {prim} --")
        report(ordered[:mid], "  النصف الأول")
        report(ordered[mid:], "  النصف الثاني")

    print("\n== حساسية التكلفة ==")
    print("   الباك تست بلا عمولة. كل 0.05R تكلفة للصفقة تُخصم من متوسط R.")
    for prim, fills in results.items():
        if not fills:
            continue
        avg = statistics.mean(t.r_multiple for t in fills)
        room = avg / 0.05
        verdict = "يتحمل تكلفة" if avg > 0.05 else "لا يتحمل أي تكلفة"
        print(f"  {prim}: متوسط R={avg:+.3f} | يحتمل حتى {max(room, 0):.2f} × 0.05R → {verdict}")

    print("\n== تصحيح المقارنات المتعددة ==")
    print("   فحصتُ خلايا كثيرة؛ واحدة معنوية بالصدفة أمر متوقع.")
    print("   بونفيروني لعائلات بحجم 3 و 9 على مستوى 0.05:")
    for k in (3, 9):
        # A CI excludes zero iff |t| exceeds the critical value; the bootstrap
        # equivalent is to widen the interval to the 1 - 0.05/k level.
        level = 1 - 0.05 / k
        print(f"  عائلة بحجم {k}: مستوى مطلوب {level:.4f}")
        for prim, fills in results.items():
            for d in ("buy", "sell"):
                sub = [t.r_multiple for t in fills if t.direction == d]
                lo, hi = bootstrap_ci(sub, level=level)
                if lo is None:
                    continue
                if lo > 0:
                    print(f"    {prim} {d}: صامد ✅ CI=[{lo:+.3f},{hi:+.3f}]")
    print("   أي خلية لم تظهر هنا لم تصمد بعد التصحيح.")

    print("\n== الفحص الحاسم: هل حافة الشراء مجرد بيتا؟ ==")
    print("   الذهب ارتفع كثيراً في العيّنة، فأي استراتيجية شرائية تربح بلا مهارة.")
    print("   لو كانت الحافة بيتا لاختفت في الفترات الهابطة. النظام هنا حركة")
    print("   السعر الفعلية حول الصفقة، لا نتيجتها.")
    for prim, fills in results.items():
        if not fills or prim not in series:
            continue
        bars = series[prim].bars
        stamps = [b.time for b in bars]
        import bisect

        def regime_at(t):
            idx = bisect.bisect_left(stamps, t)
            lo = max(0, idx - 40)
            hi = min(len(bars) - 1, idx + 40)
            if hi <= lo:
                return None
            return bars[hi].close - bars[lo].close

        print(f"-- {prim} --")
        for d in ("buy", "sell"):
            sub = [t for t in fills if t.direction == d]
            rising = [t for t in sub if (regime_at(t.time) or 0) > 0]
            falling = [t for t in sub if (regime_at(t.time) or 0) <= 0]
            for label, grp in (("سوق صاعد", rising), ("سوق هابط", falling)):
                if len(grp) >= 20:
                    rs = [t.r_multiple for t in grp]
                    print(f"    {d:4s} {label}: n={len(rs):4d} avgR={statistics.mean(rs):+.3f} "
                          f"PF={profit_factor(rs):.2f}")

    print("\n== الحكم النهائي ==")
    print("   الحافة التي تنجو من كل ما سبق تُقبل. ما دون ذلك يُرفض.")

    print("\n== الخلية الوحيدة التي نجت: استمرار BOS على H1 ==")
    print("   فحصتها لأنها كانت الوحيدة بواجهة ثقة تستبعد الصفر.")
    fills = results.get("H1", [])
    cell = [t for t in fills if "BOS" in t.setup or "استمرار" in t.setup]
    if len(cell) >= 20:
        bars = series["H1"].bars
        stamps = [b.time for b in bars]
        import bisect

        def regime(t):
            i = bisect.bisect_left(stamps, t.time)
            lo, hi = max(0, i - 40), min(len(bars) - 1, i + 40)
            return bars[hi].close - bars[lo].close if hi > lo else 0.0

        for label, grp in (("سوق صاعد", [t for t in cell if regime(t) > 0]),
                           ("سوق هابط", [t for t in cell if regime(t) <= 0])):
            if len(grp) >= 10:
                rs = [t.r_multiple for t in grp]
                lo, hi = bootstrap_ci(rs)
                print(f"    {label}: n={len(rs):3d} avgR={statistics.mean(rs):+.3f} "
                      f"PF={profit_factor(rs):.2f} CI=[{lo:+.3f},{hi:+.3f}]")
        ordered = sorted(cell, key=lambda t: t.time)
        mid = len(ordered) // 2
        for label, grp in (("النصف الأول", ordered[:mid]), ("النصف الثاني", ordered[mid:])):
            rs = [t.r_multiple for t in grp]
            if len(rs) >= 10:
                print(f"    {label}: n={len(rs):3d} avgR={statistics.mean(rs):+.3f} "
                      f"PF={profit_factor(rs):.2f}")
        print("   لو بقيت موجبة في السوق الهابطة وفي النصفين، فهي حافة حقيقية.")


if __name__ == "__main__":
    main()
