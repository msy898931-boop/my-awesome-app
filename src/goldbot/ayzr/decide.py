"""AYZR's decision layer: the model proposes, the arithmetic disposes.

This is the part that makes an AI-driven trade honest. The model may pick a
direction and select levels, but nothing it returns reaches the journal until it
has survived four arithmetic checks against the fact sheet:

  1. **Side** — the stop is on the correct side of the entry, and the target on
     the far side. A plan that fails this is not a plan.
  2. **Floor** — the implied reward-to-risk clears AYZR's own minimum.
  3. **Provenance** — the entry sits inside a real zone (an unfilled FVG or an
     unmitigated order block) or on a named structural level from the sheet. A
     level the model cannot point to in the facts is treated as invented.
  4. **Reachability** — the entry is not absurdly far from the current price,
     which is the shape an invented level usually takes.

A plan that fails is *recorded as rejected with its reason*, never silently
repaired. The reason matters: when AYZR's month is reviewed, "the model
proposed 12 trades, 5 were rejected for a bad stop side" is a finding, and it is
lost the moment a rejection is swallowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# How far from the last close an entry may sit before it is called invented,
# as a multiple of the primary frame's ATR. An SMC entry is a return to a zone
# near price; a "level" many ATRs away is the model padding the sheet.
MAX_ENTRY_ATR = 6.0


@dataclass
class Decision:
    """AYZR's read of the picture, and the trade it would take (or none)."""

    configured: bool = False
    model: str = ""
    market_state: str = ""
    bias: str = "neutral"
    technical: str = ""
    fundamental: str = ""
    reasoning: str = ""
    scenarios: dict = field(default_factory=dict)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    trade: dict = field(default_factory=dict)
    rejected: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def has_trade(self) -> bool:
        return self.trade.get("direction") in {"buy", "sell"}

    def as_dict(self) -> dict:
        d = self.__dict__.copy()
        d["has_trade"] = self.has_trade
        return d


def decide(
    facts: dict,
    raw: dict | None,
    *,
    model: str = "",
    min_rr: float = 2.0,
    error: str = "",
) -> Decision:
    """Turn a model's JSON into a validated :class:`Decision`.

    ``raw`` is ``None`` when the model could not be reached; the returned
    decision then carries ``error`` and no trade, which is the honest outcome —
    an unreachable model is not a "no trade" call, and the journal records the
    difference through ``error``.
    """
    if raw is None:
        return Decision(configured=False, model="", error=error)

    scenarios = raw.get("scenarios")
    trade_raw = raw.get("trade")
    d = Decision(
        configured=True,
        model=model,
        market_state=_s(raw.get("market_state")),
        bias=_norm_bias(raw.get("bias")),
        technical=_s(raw.get("technical")),
        fundamental=_s(raw.get("fundamental")),
        reasoning=_s(raw.get("reasoning")),
        scenarios=scenarios if isinstance(scenarios, dict) else {},
        confidence=_clamp(raw.get("confidence"), 0.0, 1.0),
        warnings=[str(w) for w in (raw.get("warnings") or [])][:6],
    )
    d.trade, d.rejected = validate_trade(
        trade_raw if isinstance(trade_raw, dict) else {}, facts, min_rr
    )
    return d


def validate_trade(trade: dict, facts: dict, min_rr: float) -> tuple[dict, list[str]]:
    """Check a proposed trade against the fact sheet.

    Returns ``(clean_trade, reasons)``. ``clean_trade`` is ``{}`` when the trade
    was rejected or was explicitly "none"; ``reasons`` is the trail either way.
    """
    direction = str(trade.get("direction") or "none").lower()
    if direction not in {"buy", "sell"}:
        return {}, []

    reasons: list[str] = []
    try:
        entry = float(trade["entry"])
        stop = float(trade["stop"])
        target = float(trade["target"])
    except (KeyError, TypeError, ValueError):
        return {}, ["صفقة بلا أرقام صالحة"]

    risk = abs(entry - stop)
    if risk <= 0:
        return {}, ["الوقف عند الدخول (مخاطرة صفرية)"]

    reward = abs(target - entry)
    rr = reward / risk
    if rr < min_rr:
        reasons.append(f"R:R {rr:.2f} دون الحد {min_rr:.2f}")

    if direction == "buy":
        if not stop < entry < target:
            reasons.append("ترتيب أرقام خاطئ للشراء (يجب وقف < دخول < هدف)")
    else:
        if not target < entry < stop:
            reasons.append("ترتيب أرقام خاطئ للبيع (يجب هدف < دخول < وقف)")

    zone = _zone_for_entry(facts, entry, direction)
    if zone is None:
        reasons.append("الدخول ليس داخل منطقة FVG/OB ولا عند مستوى بنيوي من الحقائق")

    atr = _primary_atr(facts)
    close = _primary_close(facts)
    if atr and close is not None and abs(entry - close) > MAX_ENTRY_ATR * atr:
        reasons.append(f"الدخول بعيد جداً عن السعر ({abs(entry - close) / atr:.1f}× ATR)")

    if reasons:
        return {}, reasons

    return (
        {
            "direction": direction,
            "entry": round(entry, 5),
            "stop": round(stop, 5),
            "target": round(target, 5),
            "rr": round(rr, 3),
            "zone": _s(trade.get("zone")),
            "why": _s(trade.get("why")),
            "validated": True,
        },
        [],
    )


# ── fact-sheet helpers ───────────────────────────────────────────────────


def _frames(facts: dict) -> dict:
    frames = facts.get("timeframes")
    return frames if isinstance(frames, dict) else {}


def _primary_frame(facts: dict) -> dict:
    frames = _frames(facts)
    for tf in ("M15", "M30", "H1"):
        if tf in frames:
            return frames[tf]
    return next(iter(frames.values()), {}) if frames else {}


def _primary_close(facts: dict):
    close = _primary_frame(facts).get("last_close")
    return close if isinstance(close, (int, float)) else None


def _primary_atr(facts: dict):
    atr = _primary_frame(facts).get("atr")
    return atr if isinstance(atr, (int, float)) and atr > 0 else None


def _zone_for_entry(facts: dict, entry: float, direction: str) -> str | None:
    """The name of the zone/level the entry sits on, or ``None`` if invented.

    Checks, across every frame AYZR read:
      * unfilled fair-value gaps (the entry inside the gap);
      * unmitigated order blocks;
      * the named structural levels (last swing high/low) within one ATR.
    """
    for tf, f in _frames(facts).items():
        for g in f.get("fair_value_gaps") or []:
            if g.get("filled"):
                continue
            lo, hi = _bounds(g)
            if lo is not None and hi is not None and lo <= entry <= hi:
                return f"{tf} FVG"
        for o in f.get("order_blocks") or []:
            if o.get("mitigated"):
                continue
            lo, hi = _bounds(o)
            if lo is not None and hi is not None and lo <= entry <= hi:
                return f"{tf} OB"
        atr = f.get("atr") if isinstance(f.get("atr"), (int, float)) else None
        if atr:
            for key, name in (("last_swing_high", "قمة"), ("last_swing_low", "قاع")):
                lvl = f.get(key)
                if isinstance(lvl, (int, float)) and abs(entry - lvl) <= atr:
                    return f"{tf} {name}"
    return None


def _bounds(zone: dict) -> tuple[float | None, float | None]:
    raw_lo = zone.get("bottom", zone.get("low"))
    raw_hi = zone.get("top", zone.get("high"))
    if raw_lo is None or raw_hi is None:
        return None, None
    try:
        lo = float(raw_lo)
        hi = float(raw_hi)
    except (TypeError, ValueError):
        return None, None
    return (min(lo, hi), max(lo, hi))


def _s(value) -> str:
    return str(value).strip() if value is not None else ""


def _norm_bias(value) -> str:
    text = _s(value).lower()
    return text if text in {"bullish", "bearish", "neutral"} else "neutral"


def _clamp(value, lo: float, hi: float) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except (TypeError, ValueError):
        return lo
