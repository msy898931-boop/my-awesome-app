"""Study 3: is the strategy's return *timing*, or gold's drift in disguise?

Study 2 asked whether the strategy made money. It found the setup is beta to
gold: it wins when the market rises and loses when it falls. This study makes
that finding exact, by putting the strategy next to **matched controls** — the
same trade geometry, the same costs, the same fill and horizon rules, only the
entry *times* drawn at random.

Two controls, because they answer two questions:

* **random** — could any set of entries with this geometry have done as well?
* **regime** — a real trade is compared only against synthetic entries drawn
  from bars in *its own* trend regime, so the drift credit it inherited is held
  constant and only the timing within the regime is free. This is the harsh one.

A cell is called **skill** only if its mean beats both controls at α = 0.05. If
it loses to either, it is **beta**. If there is not enough to say, it is
**inconclusive** — which is not a pass, and is never printed as one.

Run:  PYTHONPATH=src python3 research/study_control.py
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

from goldbot.backtest.control import CONTROL, skill_test
from goldbot.backtest.engine import _walk
from goldbot.config import load_settings

# A smaller surrogate count than the library default keeps a full 9-cell scan
# under a minute; the p-value resolution is still finer than α.
N = 600
SEED = 11


def load(settings):
    """The same archive-only read study 2 uses, so both studies see one sample."""
    from goldbot.data.archive import Archive
    from goldbot.data.feed import MarketFeed

    feed = MarketFeed([], archive=Archive(settings.archive_dir))
    series = {}
    for tf in ("M15", "M30", "H1", "H4"):
        s, _ = feed.series(tf, 20000, prefer_depth=True)
        if s is not None and len(s) >= 90:
            series[tf] = s
    return series


def _line(label: str, out: dict) -> str:
    if out["real_mean"] is None:
        return f"  {label:24s}: عيّنة صغيرة (n={out['n']})"
    gap_r = out["gap_random"]
    gap_g = out["gap_regime"]
    return (
        f"  {label:24s}: n={out['n']:5d} | avgR={out['real_mean']:+.3f} | "
        f"random={out['random_mean']:+.3f} p={out['random_p']:.3f} (فارق {gap_r:+.3f}) | "
        f"regime={out['regime_mean']:+.3f} p={out['regime_p']:.3f} (فارق {gap_g:+.3f}) "
        f"→ {out['verdict']}"
    )


def main():
    settings = load_settings()
    series = load(settings)
    print("عمق البيانات:")
    for tf, s in series.items():
        print(f"  {tf}: {len(s.bars)} شمعة")
    print(f"\nسيطرة مطابقة: {N} مجموعة بديلة لكل خلية، بذرة {SEED}\n")

    verdicts: dict[str, int] = {}
    for prim in ("M15", "M30", "H1"):
        if prim not in series:
            continue
        trades = _walk(series, settings.min_rr, primary_tf=prim)
        fills = [t for t in trades if t.outcome != "no_fill"]
        bars = series[prim].bars
        print(f"== {prim} == كلي + حسب الاتجاه + حسب النموذج ==")
        groups = [("كلي", fills)]
        groups += [(d, [t for t in fills if t.direction == d]) for d in ("buy", "sell")]
        for setup in sorted({t.setup for t in fills}):
            groups.append((setup[:20], [t for t in fills if t.setup == setup]))
        for label, grp in groups:
            out = skill_test(grp, bars, n=N, seed=SEED)
            print(_line(label, out))
            verdicts[out["verdict"]] = verdicts.get(out["verdict"], 0) + 1
        print()

    print("== الحكم ==")
    print("   خلية تُسمّى skill فقط إذا غلبت السيطرة العشوائية *و* سيطرة النظام.")
    print("   ما دون ذلك beta — أو inconclusive إن كانت العيّنة صغيرة.")
    print(f"   توزيع الأحكام: {verdicts}")
    if verdicts.get("skill"):
        print(f"   ⚠️ {verdicts['skill']} خلية/خلايا صمدت — راجعها يدوياً قبل الوثوق.")
    else:
        print("   لا خلية صمدت أمام السيطرة المطابقة: الاستراتيجية بيتا، لا مهارة.")
    print(f"   ملاحظة: العتبة الافتراضية للمكتبة {CONTROL} مجموعة؛ هنا {N} للسرعة.")


if __name__ == "__main__":
    main()
