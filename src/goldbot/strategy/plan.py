"""The plan builder: SMC/ICT setups, in the order a practitioner checks them.

The setups, and what each requires:

  1. **CHoCH + FVG/OB retest** — the highest-quality entry. A change of
     character on a lower frame confirms the higher-frame bias, and price
     returns to the imbalance or block that caused it. Entry at the zone, stop
     beyond the structure that invalidates it, target at the opposing liquidity.
  2. **BOS continuation** — the higher-frame trend is intact and a lower frame
     breaks structure in the same direction. Entry at the broken level, stop
     beyond the last opposing swing.
  3. **Liquidity sweep reversal** — price sweeps an equal-highs/lows pool and
     closes back inside, against a stretched position in the dealing range.

Each candidate is scored on how many independent facts agree. The best is
returned; if none meets the minimum, no plan is emitted and the reasons are
listed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Frames, coarse to fine. Bias comes from the top, entry from the bottom.
HTF = ("H4", "H1")
LTF = ("M30", "M15", "M5")

# A zone wider than this many ATRs is not a zone, it is the whole range.
MAX_ZONE_ATR = 1.5
# A stop closer than this many ATRs is inside the noise: it would be taken out
# by ordinary movement rather than by the market invalidating the idea. Widening
# to this floor keeps the R:R honest instead of flattering.
MIN_STOP_ATR = 0.5
# On gold, spread and normal tick noise make a stop tighter than this fraction
# of price meaningless, whatever the ATR says. At $4000 this is $6.
MIN_STOP_PCT = 0.0015
# An R:R above this is not a better trade, it is a sign the target sits on a
# pool too far to be reached before the idea expires. Refused rather than shown.
MAX_RR = 8.0


@dataclass
class Plan:
    """A concrete, checkable trade idea. ``direction`` is ``none`` when there is none."""

    direction: str = "none"
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    rr: float = 0.0
    setup: str = ""
    reason: str = ""
    score: int = 0
    factors: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    levels: dict = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.direction in {"buy", "sell"} and self.rr > 0

    def as_dict(self) -> dict:
        return asdict(self)


def pick_setup(facts: dict, min_rr: float = 2.0) -> Plan:
    """Find the best-supported setup across the frames, or report why not.

    The top-down rule is enforced here rather than trusted to each setup: a
    counter-trend plan is refused before it is even built. Reading the frames
    and then trading against their verdict is the fastest way to a losing
    book, and it is exactly what produced a 6% win rate on the buy side of a
    falling market.
    """
    frames = facts.get("timeframes", {})
    if not frames:
        return Plan(rejected=["لا توجد فريمات كافية للقراءة"])

    confluence = facts.get("confluence") or {}
    bias = confluence.get("bias", "neutral")
    alignment = confluence.get("alignment", 0.0)

    # A neutral read means the frames disagree; there is no trend to join, so
    # both directions stay open and the setups must stand on their own evidence.
    if bias == "neutral":
        directions: tuple[str, ...] = ("buy", "sell")
    else:
        directions = ("buy",) if bias == "bullish" else ("sell",)

    candidates: list[Plan] = []
    rejected: list[str] = []
    for direction in ("buy", "sell"):
        if direction not in directions:
            rejected.append(f"{direction}: مرفوض — يخالف الانحياز العام ({bias})")
            continue

        plan = _try_choch_retest(frames, direction, min_rr)
        if plan:
            candidates.append(plan)
        else:
            rejected.append(f"{direction}: لا يوجد CHoCH + إعادة اختبار FVG/OB صالح")

        plan = _try_bos_continuation(frames, direction, bias, min_rr)
        if plan:
            candidates.append(plan)
        else:
            rejected.append(f"{direction}: لا يوجد استمرار BOS مطابق للانحياز")

        plan = _try_sweep_reversal(frames, direction, min_rr)
        if plan:
            candidates.append(plan)
        else:
            rejected.append(f"{direction}: لا يوجد كنس سيولة وانعكاس مؤكّد")

    if not candidates:
        return Plan(rejected=rejected, levels={"bias": bias, "alignment": alignment})

    best = max(candidates, key=lambda p: (p.score, p.rr))
    best.levels.update({"bias": bias, "alignment": alignment})
    best.rejected = [r for r in rejected if best.direction not in r]
    return best


# ── setup 1: CHoCH + FVG/OB retest ───────────────────────────────────────


def _try_choch_retest(frames: dict, direction: str, min_rr: float) -> Plan | None:
    htf_ok = any(
        frames.get(tf, {}).get("trend") == ("bullish" if direction == "buy" else "bearish")
        for tf in HTF
    )
    if not htf_ok:
        return None

    for tf in LTF:
        f = frames.get(tf)
        if not f:
            continue
        event = f.get("last_event") or {}
        want = "bullish" if direction == "buy" else "bearish"
        if event.get("type") != "CHoCH" or event.get("direction") != want:
            continue

        atr = f.get("atr") or 0.0
        zone = _best_zone(f, direction, atr)
        if zone is None:
            continue
        entry, stop_anchor, kind, far_edge = zone
        plan = _candidate(
            direction,
            entry,
            stop_anchor,
            atr,
            frames,
            min_rr,
            setup="CHoCH + إعادة اختبار FVG/OB",
            reason=(
                f"تغيّر شخصية {want} على {tf} يؤكد انحياز الفريم الأعلى، "
                f"والسعر عاد إلى منطقة {'الفجوة السعرية' if kind == 'FVG' else 'كتلة الأوامر'} "
                f"بين {far_edge:.2f} و {entry:.2f}"
            ),
            score=3
            + (
                1
                if f.get("premium_discount", {}).get("zone")
                == ("discount" if direction == "buy" else "premium")
                else 0
            ),
            factors=[f"CHoCH {want} على {tf}", f"منطقة {kind} عند {entry:.2f}"],
            timeframe=tf,
        )
        if plan:
            return plan
    return None


# ── setup 2: BOS continuation ────────────────────────────────────────────


def _try_bos_continuation(frames: dict, direction: str, bias: str, min_rr: float) -> Plan | None:
    """BOS continuation, entered on the *retest* of the broken level.

    The break itself is not the entry — buying the break is a chase. The entry
    is the broken level acting as new support/resistance, so the order rests
    there and waits for price to come back. If price runs away without
    retesting, there is no trade, which is the honest outcome.
    """
    want_bias = "bullish" if direction == "buy" else "bearish"
    if bias != want_bias:
        return None
    for tf in LTF:
        f = frames.get(tf)
        if not f:
            continue
        event = f.get("last_event") or {}
        if event.get("type") != "BOS" or event.get("direction") != want_bias:
            continue
        level = event.get("level")
        if not level:
            continue
        close = f.get("last_close") or 0.0
        # The retest entry is the broken level, and it must lie behind price.
        if direction == "buy" and not level < close:
            continue
        if direction == "sell" and not level > close:
            continue
        atr = f.get("atr") or 0.0
        entry = float(level)
        stop_anchor = f.get("last_swing_low") if direction == "buy" else f.get("last_swing_high")
        if stop_anchor is None:
            continue
        plan = _candidate(
            direction,
            entry,
            float(stop_anchor),
            atr,
            frames,
            min_rr,
            setup="استمرار BOS (إعادة اختبار المستوى)",
            reason=(
                f"كسر بنية {want_bias} على {tf} عند {entry:.2f} مع انحياز عام {bias}، "
                f"والدخول على إعادة اختبار المستوى المكسور كدعم/مقاومة جديد"
            ),
            score=2,
            factors=[f"BOS على {tf} عند {entry:.2f}", "إعادة اختبار المستوى المكسور"],
            timeframe=tf,
        )
        if plan:
            return plan
    return None


# ── setup 3: liquidity sweep reversal ────────────────────────────────────


def _try_sweep_reversal(frames: dict, direction: str, min_rr: float) -> Plan | None:
    want_sweep = "buy_side_taken" if direction == "sell" else "sell_side_taken"
    for tf in LTF:
        f = frames.get(tf)
        if not f:
            continue
        sweeps = f.get("sweeps") or []
        hit = next((s for s in sweeps if s.get("side") == want_sweep), None)
        if not hit:
            continue
        pd_zone = (f.get("premium_discount") or {}).get("zone")
        # A reversal is only credible from a stretched part of the range.
        if direction == "buy" and pd_zone != "discount":
            continue
        if direction == "sell" and pd_zone != "premium":
            continue
        level = float(hit["level"])
        atr = f.get("atr") or 0.0
        entry = f.get("last_close") or level
        stop_anchor = level - atr * 0.5 if direction == "buy" else level + atr * 0.5
        plan = _candidate(
            direction,
            float(entry),
            float(stop_anchor),
            atr,
            frames,
            min_rr,
            setup="انعكاس بعد كنس سيولة",
            reason=(
                f"كنس سيولة عند {level:.2f} على {tf} من منطقة {pd_zone}، "
                f"مع إغلاق عائد داخل النطاق — إشارة استنزاف حركة"
            ),
            score=2,
            factors=[f"كنس {want_sweep} عند {level:.2f}", f"منطقة {pd_zone}"],
            timeframe=tf,
        )
        if plan:
            return plan
    return None


# ── helpers ──────────────────────────────────────────────────────────────


def _best_zone(f: dict, direction: str, atr: float) -> tuple | None:
    """The nearest zone the price must *retrace into*, or ``None``.

    Returns ``(entry, stop_anchor, kind, far_edge)``.

    The retracement constraint is the whole point. A buy zone is only valid if
    it sits **below** the current price: the setup is price coming back down
    into demand, not price chasing a level above it. Selecting the nearest zone
    regardless of side is what turns a retracement strategy into a momentum
    chase, and it is the single most damaging error this function can make.

    A zone wider than ``MAX_ZONE_ATR`` is also refused: that is not a level,
    it is the whole range.
    """
    kind_want = "bullish" if direction == "buy" else "bearish"
    zones: list[tuple[float, float, str]] = []
    for gap in f.get("fair_value_gaps") or []:
        if gap.get("side") == kind_want and not gap.get("filled"):
            zones.append((float(gap["top"]), float(gap["bottom"]), "FVG"))
    for ob in f.get("order_blocks") or []:
        if ob.get("side") == kind_want and not ob.get("mitigated"):
            zones.append((float(ob["top"]), float(ob["bottom"]), "OB"))
    if not zones:
        return None

    close = f.get("last_close") or 0.0
    if direction == "buy":
        # Only zones at or below price: price must fall back into them.
        zones = [z for z in zones if z[0] <= close]
        zones.sort(key=lambda z: close - z[0])
    else:
        # Only zones at or above price: price must rise back into them.
        zones = [z for z in zones if z[1] >= close]
        zones.sort(key=lambda z: z[1] - close)
    if not zones:
        return None

    top, bottom, kind = zones[0]
    if atr and (top - bottom) > atr * MAX_ZONE_ATR:
        return None
    if direction == "buy":
        entry = top
        stop_anchor = bottom - (atr * 0.3 if atr else 0.0)
    else:
        entry = bottom
        stop_anchor = top + (atr * 0.3 if atr else 0.0)
    return (entry, stop_anchor, kind, bottom if direction == "buy" else top)


def _opposing_liquidity(
    frames: dict, direction: str, entry: float, stop: float = 0.0, min_rr: float = 2.0
) -> float | None:
    """A resting pool ahead of entry that actually pays for the risk.

    The nearest pool is not automatically the target: with a stop set at a
    realistic distance, a pool two dollars away yields an R:R no methodology
    would accept. So the pools are sorted by distance and the first one that
    clears ``min_rr`` is chosen. If none clears it, there is no target — and
    therefore no trade, which is the honest answer.
    """
    pools: list[float] = []
    for f in frames.values():
        liq = f.get("liquidity") or {}
        if direction == "buy":
            pools.extend(float(c["price"]) for c in liq.get("equal_highs", []))
            if f.get("last_swing_high"):
                pools.append(float(f["last_swing_high"]))
        else:
            pools.extend(float(c["price"]) for c in liq.get("equal_lows", []))
            if f.get("last_swing_low"):
                pools.append(float(f["last_swing_low"]))

    ahead = sorted(
        (p for p in pools if (p > entry if direction == "buy" else p < entry)),
        reverse=(direction != "buy"),
    )
    if not ahead:
        return None
    if stop and abs(entry - stop) > 0:
        risk = abs(entry - stop)
        for pool in ahead:
            if abs(pool - entry) / risk >= min_rr:
                return pool
        return None
    return ahead[0]


def _stop_for(direction: str, entry: float, stop_anchor: float, atr: float) -> float | None:
    """The stop, pushed out to a realistic floor. ``None`` if the anchor is wrong-side."""
    floor = max(atr * MIN_STOP_ATR if atr else 0.0, entry * MIN_STOP_PCT)
    if direction == "buy":
        stop = min(stop_anchor, entry - floor)
        return stop if stop < entry else None
    stop = max(stop_anchor, entry + floor)
    return stop if stop > entry else None


def _candidate(
    direction: str,
    entry: float,
    stop_anchor: float,
    atr: float,
    frames: dict,
    min_rr: float,
    setup: str,
    reason: str,
    score: int,
    factors: list[str],
    timeframe: str,
) -> Plan | None:
    """Stop first, then a target that clears the minimum R:R, then validate.

    Ordering matters: choosing the target before the stop is what produces the
    flattering 1:13 that no trader would take. Here the stop is fixed first and
    the target must *earn* its distance.
    """
    stop = _stop_for(direction, entry, stop_anchor, atr)
    if stop is None:
        return None
    target = _opposing_liquidity(frames, direction, entry, stop, min_rr)
    if target is None:
        return None

    risk = abs(entry - stop)
    reward = abs(target - entry)
    if risk <= 0:
        return None
    rr = round(reward / risk, 2)
    if rr < min_rr or rr > MAX_RR:
        return None

    return Plan(
        direction=direction,
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        rr=rr,
        setup=setup,
        reason=reason,
        score=score,
        factors=factors,
        levels={"timeframe": timeframe, "atr": round(atr, 2) if atr else None},
    )


def build_plan(facts: dict, min_rr: float = 2.0) -> Plan:
    """Public entry point. Returns an empty plan when nothing qualifies."""
    return pick_setup(facts, min_rr)
