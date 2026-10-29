"""The matched control: is the strategy's result timing, or just gold's drift?

A positive total R proves nothing on its own. Gold trended up across the sample,
so *any* long-biased set of entries inherits some of that drift, and a strategy
that only ever looks "right in hindsight" will look profitable for a reason that
has nothing to do with its logic. The question this module answers is narrower
and harsher than "did it make money":

    Given the *same trade geometry* — same directions, same risk distance, same
    reward-to-risk, same cost, same fill and horizon rules — but with the
    **entry times drawn at random**, how often does chance do as well?

Whatever chance does is the control. The strategy's result only counts as
**skill** to the extent it beats its control, and the gap between them is the
only number worth quoting.

Two controls, because they defeat two different confounds:

* **random** — entry times drawn uniformly from the walkable window. Defeats
  "the geometry alone was lucky", but not "you were long in an uptrend".
* **regime** — entry times drawn only from bars whose *trend regime matches the
  real trade's* (rising for a trade taken when the market was rising, and so
  on). This cancels the drift credit: a buy is compared against other buys made
  into the same kind of market, so the trend is held constant and only the
  timing within it remains free.

A strategy passes only if it beats **both**. The regime control is the decisive
one: it is the test that separated a real edge from "long-gold in disguise" in
the historical study, and it is the reason this module exists.

Everything is deterministic given a seed, stdlib-only, and reads no network.
"""

from __future__ import annotations

import random
import statistics
from collections.abc import Sequence
from typing import Any

from ..models import Bar
from .engine import COST_FRACTION, HORIZON_BARS, Trade, evaluate_trade

# Surrogate sets drawn per control. The p-value resolution is 1/(CONTROL+1),
# so 2000 gives enough to call a comfortable edge without a long run.
CONTROL = 2000
SEED = 11
# Bars before a surrogate entry is allowed, so the window is inside the sample.
WARMUP = 60
# Bars either side used to call the regime at a point (trailing only: causal).
REGIME_WINDOW = 40


def regime_flags(bars: Sequence[Bar], window: int = REGIME_WINDOW) -> list[int]:
    """``+1`` when the market rose over the trailing window ending at each bar.

    Trailing, never centred: the flag at bar *i* must be readable without
    knowing the future, or the control would be allowed information the
    strategy never had. A flat stretch counts as falling, matching the study.
    """
    flags: list[int] = []
    for i in range(len(bars)):
        lo = max(0, i - window)
        rose = bars[i].close - bars[lo].close
        flags.append(1 if rose > 0 else -1)
    return flags


def _surrogate(
    trade: Trade,
    bars: Sequence[Bar],
    index: int,
    cost_fraction: float,
    horizon: int,
) -> Trade | None:
    """One control trade: the real trade's geometry at a different entry time.

    The risk distance and the reward-to-risk are copied from ``trade`` exactly,
    so only *when* the entry happened is free. The reference price is the close
    of the drawn bar — a level the market was actually at — so the limit fill
    is judged against real prices, not an invented one.
    """
    risk = abs(trade.entry - trade.stop)
    if risk <= 0 or index >= len(bars) - horizon - 1:
        return None
    ref = bars[index].close
    if trade.direction == "buy":
        entry, stop, target = ref, ref - risk, ref + risk * trade.rr
    else:
        entry, stop, target = ref, ref + risk, ref - risk * trade.rr
    control = Trade(
        index=index,
        time=bars[index].time,
        direction=trade.direction,
        entry=round(entry, 4),
        stop=round(stop, 4),
        target=round(target, 4),
        rr=trade.rr,
        setup="control",
        timeframe=trade.timeframe,
    )
    return evaluate_trade(control, list(bars[index + 1 : index + 1 + horizon]), cost_fraction)


def _mean_r(trades: Sequence[Trade]) -> float | None:
    rs = [t.r_multiple for t in trades if t.outcome != "no_fill"]
    return statistics.mean(rs) if rs else None


def surrogate_mean(
    trades: Sequence[Trade],
    bars: Sequence[Bar],
    rng: random.Random,
    mode: str,
    cost_fraction: float = COST_FRACTION,
    horizon: int = HORIZON_BARS,
    eligible: Sequence[int] | None = None,
) -> tuple[float | None, int]:
    """Mean R of one surrogate set, matched to ``trades`` by every frozen field.

    Returns ``(mean, filled)`` — ``filled`` is how many surrogates actually
    filled, since a control that never fills is not a comparison. ``mode`` is
    ``"random"`` (times anywhere) or ``"regime"`` (times only in the real
    trade's regime).

    ``eligible`` is the set of bars the *strategy itself* could have signalled
    on. It defaults to the walkable window, and it must not be narrower than the
    real trades' own span: if the real entries all sit in one stretch of the
    sample and the control may draw from a different stretch, a genuine
    difference in drift between the two — nothing to do with timing — shows up
    as skill. The control has to be exchangeable with the strategy, or it is
    measuring the window, not the timing.
    """
    if not trades or len(bars) <= WARMUP + horizon + 1:
        return None, 0
    walkable = (
        list(eligible) if eligible is not None else list(range(WARMUP, len(bars) - horizon - 1))
    )
    # Guard the draw window against the horizon, whatever was passed in.
    walkable = [i for i in walkable if WARMUP <= i < len(bars) - horizon - 1]
    if not walkable:
        return None, 0

    flags = regime_flags(bars) if mode == "regime" else None
    pools: dict[int, list[int]] = {}
    if flags is not None:
        for want in (1, -1):
            pools[want] = [i for i in walkable if flags[i] == want]

    filled: list[Trade] = []
    for trade in trades:
        pool = walkable if flags is None else pools.get(trade_regime(trade, bars), walkable)
        if not pool:
            pool = walkable
        index = pool[rng.randrange(len(pool))]
        surrogate = _surrogate(trade, bars, index, cost_fraction, horizon)
        if surrogate is not None and surrogate.outcome != "no_fill":
            filled.append(surrogate)
    return _mean_r(filled), len(filled)


def trade_regime(trade: Trade, bars: Sequence[Bar]) -> int:
    """The regime of the market at a real trade's entry bar.

    The trade is matched to the nearest bar at or before its time, so the
    regime it is compared against is the one a trader could have seen without a
    time machine. Defaults to *falling* when the bar cannot be located, the
    same convention :func:`regime_flags` uses for a flat stretch.
    """
    if not bars:
        return -1
    lo, hi = 0, len(bars) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if bars[mid].time <= trade.time:
            lo = mid
        else:
            hi = mid - 1
    window = max(0, lo - REGIME_WINDOW)
    return 1 if bars[lo].close - bars[window].close > 0 else -1


def bootstrap_ci(
    values: Sequence[float], n: int = CONTROL, seed: int = SEED, level: float = 0.95
) -> tuple[float | None, float | None]:
    """Percentile bootstrap CI for the mean, distribution-free."""
    if len(values) < 10:
        return None, None
    # ``random`` only, never a cryptographic source: this draws bootstrap
    # resamples to estimate a confidence interval, and reproducibility (a fixed
    # seed) is the requirement, not unpredictability.
    rng = random.Random(seed)  # nosec B311
    means = [
        statistics.mean([values[rng.randrange(len(values))] for _ in range(len(values))])
        for _ in range(n)
    ]
    means.sort()
    tail = (1 - level) / 2
    return means[int(tail * n)], means[int((1 - tail) * n)]


def _p_value(control_means: Sequence[float], real_mean: float) -> float:
    """One-sided p: how often chance matched or beat the strategy.

    The ``+1`` in both terms is the standard correction that keeps the estimate
    off zero — with 2000 draws the answer should never read exactly 0.000 and
    pretend to be certainty.
    """
    beaten = sum(1 for m in control_means if m >= real_mean)
    return (beaten + 1) / (len(control_means) + 1)


def _eligible_window(trades: Sequence[Trade], bars: Sequence[Bar], horizon: int) -> list[int]:
    """Bars the strategy could have signalled on, given when it actually traded.

    The window is the span the real entries occupy — from the first to the last,
    padded by the horizon so a control entry still has room to resolve. Drawing
    controls from a wider window would let a difference in drift between the
    two stretches masquerade as timing skill; drawing from a narrower one would
    let the strategy hide from the market it actually traded in.
    """
    if not trades:
        return []
    stamps = [b.time for b in bars]
    times = sorted(t.time for t in trades)
    lo = _index_at(stamps, times[0])
    hi = _index_at(stamps, times[-1])
    return [i for i in range(lo, hi + 1) if WARMUP <= i < len(bars) - horizon - 1]


def _index_at(stamps: Sequence[int], when: int) -> int:
    """The index of the last bar at or before ``when`` (binary search, causal)."""
    lo, hi = 0, len(stamps) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if stamps[mid] <= when:
            lo = mid
        else:
            hi = mid - 1
    return lo


def skill_test(
    trades: Sequence[Trade],
    bars: Sequence[Bar],
    n: int = CONTROL,
    seed: int = SEED,
    cost_fraction: float = COST_FRACTION,
    horizon: int = HORIZON_BARS,
    alpha: float = 0.05,
) -> dict:
    """Measure the strategy against both controls and return the verdict.

    The result is a plain dict so a study script or a test can read it without
    importing a dataclass. ``verdict`` is ``skill`` only when the real mean is
    positive *and* beats both controls at ``alpha``; ``beta`` when it loses to
    either; ``inconclusive`` when there is not enough to say. A verdict of
    ``inconclusive`` is not a pass — it is the honest name for "cannot tell",
    and it must never be reported as one.
    """
    real = [t.r_multiple for t in trades if t.outcome != "no_fill"]
    out: dict[str, Any] = {
        "n": len(real),
        "real_mean": None,
        "real_ci": (None, None),
        "random_mean": None,
        "random_p": None,
        "regime_mean": None,
        "regime_p": None,
        "gap_random": None,
        "gap_regime": None,
        "verdict": "inconclusive",
    }
    if len(real) < 10:
        return out

    real_mean = statistics.mean(real)
    out["real_mean"] = real_mean
    out["real_ci"] = bootstrap_ci(real, seed=seed)

    eligible = _eligible_window(trades, bars, horizon)
    # A seeded generator is deliberate: an unchanged input must give an
    # unchanged verdict, so a study can be reproduced and a test asserted.
    rng = random.Random(seed)  # nosec B311
    random_means: list[float] = []
    regime_means: list[float] = []
    for _ in range(n):
        r_mean, _ = surrogate_mean(trades, bars, rng, "random", cost_fraction, horizon, eligible)
        if r_mean is not None:
            random_means.append(r_mean)
        g_mean, _ = surrogate_mean(trades, bars, rng, "regime", cost_fraction, horizon, eligible)
        if g_mean is not None:
            regime_means.append(g_mean)

    if random_means:
        out["random_mean"] = statistics.mean(random_means)
        out["random_p"] = _p_value(random_means, real_mean)
        out["gap_random"] = real_mean - out["random_mean"]
    if regime_means:
        out["regime_mean"] = statistics.mean(regime_means)
        out["regime_p"] = _p_value(regime_means, real_mean)
        out["gap_regime"] = real_mean - out["regime_mean"]

    beats_random = out["random_p"] is not None and out["random_p"] < alpha
    beats_regime = out["regime_p"] is not None and out["regime_p"] < alpha
    if real_mean <= 0:
        out["verdict"] = "beta"
    elif beats_random and beats_regime:
        out["verdict"] = "skill"
    elif out["random_p"] is None or out["regime_p"] is None:
        out["verdict"] = "inconclusive"
    else:
        out["verdict"] = "beta"
    return out
