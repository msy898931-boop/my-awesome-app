"""AYZR's shadow journal — the record that makes a month mean something.

Two things are written for every signal, and they are written together on
purpose:

  * the **signal** AYZR produced, with its own entry, stop, target and R:R;
  * a **control** — the same geometry (same entry, same risk distance, same
    target distance) but a coin-flip direction.

The control is the whole point. Gold trends, so a directional engine can look
clever for a month by doing nothing more than being long during an up-month.
Scoring the signal against a matched coin flip removes that: if the two columns
end up the same, AYZR is not adding timing, whatever its total R looks like.

Outcomes are resolved later, against the real bars that followed, with the
project's own tested fill/resolution rules (``backtest.engine.evaluate_trade``):
a resting entry only fills if price traded there, and the stop is checked before
the target within each bar. A signal that never fills is recorded as ``no_fill``
— not as a win or a loss, because inventing a fill at a price the market never
traded is how a journal lies to its owner.

The file is JSONL: one append-only line per event, human-inspectable, safe to
delete. Each line is self-describing so the report never has to guess.
"""

from __future__ import annotations

import json
import time
import zlib
from pathlib import Path

from ..backtest.engine import Trade, evaluate_trade
from ..models import Bar
from .config import AyzrSettings


def signal_key(direction: str, setup: str) -> str:
    """Identity of an idea: direction + setup.

    Deliberately **not** the entry price. Entry, stop and target drift bar to
    bar, so keying on them makes one idea look new almost every cycle — which is
    how the main bot once emitted eight cards in forty minutes, all "buy".
    """
    return f"{direction}:{setup}"


def append(settings: AyzrSettings, record: dict) -> None:
    """Append one record to the journal."""
    settings.ensure_dirs()
    line = json.dumps(record, ensure_ascii=False)
    with open(settings.log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def read_all(settings: AyzrSettings) -> list[dict]:
    """Every record, oldest first. A corrupt line is skipped, not fatal."""
    path = settings.log_path
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def open_signals(settings: AyzrSettings) -> list[dict]:
    """Signals that have been logged but not yet resolved."""
    return [r for r in read_all(settings) if r.get("kind") == "signal" and r.get("outcome") is None]


def log_signal(
    settings: AyzrSettings,
    decision,
    facts: dict,
    *,
    price: float | None,
    control_seed: int | None = None,
) -> dict | None:
    """Journal a decision that carries a validated trade.

    Returns the written record, or ``None`` when there was no trade to journal
    (a "no trade" call is a real outcome and is recorded separately by
    :func:`log_no_trade`, so the denominator of the month is honest).
    """
    if not decision.has_trade:
        return None
    trade = decision.trade
    now = int(time.time())
    control_dir = _control_direction(trade["direction"], now, control_seed)
    record = {
        "kind": "signal",
        "engine": "AYZR",
        "logged_at": now,
        "tf": settings.primary_tf,
        "direction": trade["direction"],
        "setup": decision.scenarios.get("primary", {}).get("thesis", "")[:80]
        if isinstance(decision.scenarios, dict)
        else "",
        "entry": trade["entry"],
        "stop": trade["stop"],
        "target": trade["target"],
        "rr": trade["rr"],
        "zone": trade.get("zone", ""),
        "why": trade.get("why", ""),
        "confidence": decision.confidence,
        "model": decision.model,
        "bias": decision.bias,
        "price_at_signal": price,
        "outcome": None,
        "r_multiple": None,
        "bars_held": None,
        "resolved_at": None,
        "control": {
            "direction": control_dir,
            "outcome": None,
            "r_multiple": None,
        },
    }
    append(settings, record)
    return record


def log_no_trade(settings: AyzrSettings, decision, *, price: float | None) -> dict:
    """Record a "no trade" call, so the month's denominator is complete."""
    record = {
        "kind": "no_trade",
        "engine": "AYZR",
        "logged_at": int(time.time()),
        "bias": decision.bias,
        "market_state": decision.market_state[:400],
        "confidence": decision.confidence,
        "model": decision.model,
        "price_at_signal": price,
        "rejected": decision.rejected,
    }
    append(settings, record)
    return record


def resolve_open(settings: AyzrSettings, feed) -> int:
    """Resolve every open signal against the bars that have since formed.

    Returns how many signals were resolved. Resolution re-reads the tape: a
    signal is judged only on bars that *opened after* it, so the outcome cannot
    peek at the candle that produced the signal.
    """
    pending = open_signals(settings)
    if not pending:
        return 0
    series, _ = feed.series(settings.primary_tf, settings.bars)
    if series is None or not series.bars:
        return 0
    resolved = 0
    for rec in pending:
        outcome = _resolve_one(rec, series.bars, settings)
        if outcome is None:
            continue
        append(settings, {"kind": "resolution", "key": _key(rec), **outcome})
        resolved += 1
    return resolved


def _resolve_one(rec: dict, bars: list[Bar], settings: AyzrSettings) -> dict | None:
    """Resolve one signal and its control against ``bars``.

    Only bars strictly after the signal's timestamp are eligible, so the
    outcome is judged on information the signal could not have seen.
    """
    after = [b for b in bars if b.time > rec.get("logged_at", 0)]
    if len(after) < 2:
        return None

    trade = _as_trade(rec, rec["direction"])
    evaluated = evaluate_trade(trade, after, fill_window=settings.fill_window, cost_fraction=0.0)
    control = _as_trade(rec, rec["control"]["direction"])
    evaluated_control = evaluate_trade(
        control, after, fill_window=settings.fill_window, cost_fraction=0.0
    )

    # A signal still resting (never filled, horizon not reached) is left open
    # rather than scored, so a fresh idea is not marked "no_fill" prematurely.
    if evaluated.outcome == "no_fill" and len(after) < settings.fill_window:
        return None

    return {
        "direction": rec["direction"],
        "outcome": evaluated.outcome,
        "r_multiple": evaluated.r_multiple,
        "bars_held": evaluated.bars_held,
        "control": {
            "direction": rec["control"]["direction"],
            "outcome": evaluated_control.outcome,
            "r_multiple": evaluated_control.r_multiple,
        },
        "resolved_at": int(time.time()),
    }


def _as_trade(rec: dict, direction: str) -> Trade:
    return Trade(
        index=0,
        time=int(rec.get("logged_at", 0)),
        direction=direction,
        entry=float(rec["entry"]),
        stop=float(rec["stop"]),
        target=float(rec["target"]),
        rr=float(rec.get("rr") or 0.0),
        setup=str(rec.get("setup") or ""),
        timeframe=str(rec.get("tf") or ""),
    )


def _key(rec: dict) -> str:
    return f"{rec.get('logged_at')}:{rec.get('direction')}"


def _control_direction(direction: str, logged_at: int, seed: int | None = None) -> str:
    """A coin-flip direction, made deterministic so a run is reproducible.

    The control is a *random direction* with the signal's own geometry, not the
    inverse. An inverse would be systematically anti-gold, so a rising market
    would make every signal look skilful for a reason that has nothing to do with
    timing. A coin flip averages to the market itself, so the comparison reads:
    did AYZR's direction beat a coin? That is the question the beta problem
    demands, and the inverse cannot answer it.
    """
    if seed is None:
        seed = zlib.crc32(f"{direction}:{logged_at}".encode())
    return "buy" if seed % 2 == 0 else "sell"


def compact(settings: AyzrSettings) -> None:
    """Rewrite the journal folding resolutions into their signals.

    Kept as an explicit, opt-in step rather than done on every append: the
    append-only file is what makes a crash mid-month recoverable, and rewriting
    it constantly would trade that safety for a smaller file.
    """
    rows = read_all(settings)
    signals: dict[str, dict] = {}
    order: list[str] = []
    others: list[dict] = []
    for row in rows:
        if row.get("kind") == "signal":
            k = _key(row)
            signals[k] = row
            order.append(k)
        elif row.get("kind") == "resolution":
            k = str(row.get("key") or "")
            if k in signals:
                signals[k].update(
                    {
                        "outcome": row.get("outcome"),
                        "r_multiple": row.get("r_multiple"),
                        "bars_held": row.get("bars_held"),
                        "resolved_at": row.get("resolved_at"),
                        "control": row.get("control"),
                    }
                )
        else:
            others.append(row)
    settings.ensure_dirs()
    tmp = settings.log_path.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for row in others:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        for k in order:
            fh.write(json.dumps(signals[k], ensure_ascii=False) + "\n")
    Path(tmp).replace(settings.log_path)


__all__ = [
    "append",
    "compact",
    "log_no_trade",
    "log_signal",
    "open_signals",
    "read_all",
    "resolve_open",
    "signal_key",
]
