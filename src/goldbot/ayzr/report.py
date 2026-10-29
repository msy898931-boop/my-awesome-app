"""AYZR's report — what the trades did, told honestly.

This is the module that answers the user's actual question: *what happened to
the trades you gave me?* It reports three things and refuses to blur them:

  * **today** — signals logged and outcomes resolved since midnight, so the
    trader sees movement without waiting a month;
  * **all time** — the running tally against the matched coin-flip control;
  * **the verdict line** — a plain statement of whether the sample is yet large
    enough to mean anything. Below the threshold it says so in as many words,
    because a small sample that *looks* positive is the single easiest way to
    fool yourself, and this journal exists to prevent exactly that.

A word on the statistics. Every number here is a *shadow* number: no order was
ever placed, entries are judged by the project's own fill rules, and a signal
that never filled is counted as ``no_fill`` rather than scored. The comparison
that matters is not the total R — gold trends, so a directional engine earns a
positive total by accident — but the gap between the signal column and the
control column. When that gap is inside its own noise, the report says "beta".
"""

from __future__ import annotations

import calendar
import statistics
import time
from dataclasses import dataclass, field

from .config import AyzrSettings
from .journal import read_all

# Below this many *resolved* signals, no verdict is offered. A month of M15
# signals is a small sample, and the honest report is the one that says so
# instead of dressing a handful of trades as an edge.
MIN_SAMPLE = 100


@dataclass
class Stats:
    """A tally over some slice of resolved signals."""

    n: int = 0
    wins: int = 0
    losses: int = 0
    timeouts: int = 0
    no_fill: int = 0
    total_r: float = 0.0
    mean_r: float = 0.0
    control_mean_r: float = 0.0
    control_n: int = 0

    @property
    def decided(self) -> int:
        """Signals that actually resolved to a win or a loss."""
        return self.wins + self.losses

    @property
    def hit_rate(self) -> float | None:
        return (self.wins / self.decided) if self.decided else None

    @property
    def edge(self) -> float | None:
        """Mean R minus the control's mean R: the only number that means anything.

        ``None`` when the control has nothing to compare against, so a signal
        total is never shown next to an absent control as though the difference
        were zero.
        """
        if not self.control_n:
            return None
        return self.mean_r - self.control_mean_r


def compute(settings: AyzrSettings, since: int | None = None) -> Stats:
    """Tally resolved signals, optionally only those resolved after ``since``."""
    rows = read_all(settings)
    signals = _fold(rows)
    st = Stats()
    signal_rs: list[float] = []
    control_rs: list[float] = []
    for rec in signals:
        if rec.get("outcome") is None:
            continue
        if since is not None and int(rec.get("resolved_at") or 0) < since:
            continue
        st.n += 1
        outcome = rec.get("outcome")
        if outcome == "win":
            st.wins += 1
        elif outcome == "loss":
            st.losses += 1
        elif outcome == "timeout":
            st.timeouts += 1
        elif outcome == "no_fill":
            st.no_fill += 1
        if outcome != "no_fill" and rec.get("r_multiple") is not None:
            signal_rs.append(float(rec["r_multiple"]))
        ctrl = rec.get("control") or {}
        if (
            ctrl.get("outcome")
            and ctrl["outcome"] != "no_fill"
            and ctrl.get("r_multiple") is not None
        ):
            control_rs.append(float(ctrl["r_multiple"]))
    st.total_r = round(sum(signal_rs), 3)
    st.mean_r = round(statistics.mean(signal_rs), 4) if signal_rs else 0.0
    st.control_n = len(control_rs)
    st.control_mean_r = round(statistics.mean(control_rs), 4) if control_rs else 0.0
    return st


def open_count(settings: AyzrSettings) -> int:
    """Signals logged but not yet resolved — the ones still in flight."""
    return sum(1 for r in _fold(read_all(settings)) if r.get("outcome") is None)


def no_trade_count(settings: AyzrSettings, since: int | None = None) -> int:
    """How many "no trade" calls were made, so the denominator stays honest."""
    rows = read_all(settings)
    out = 0
    for r in rows:
        if r.get("kind") != "no_trade":
            continue
        if since is not None and int(r.get("logged_at") or 0) < since:
            continue
        out += 1
    return out


def verdict(st: Stats) -> str:
    """A plain statement of what the sample can and cannot support.

    Deliberately blunt. The failure mode this guards against is the reader who
    sees a positive total R in week two and concludes the engine works; the
    report must say "not yet" before it says anything else.
    """
    if st.n < MIN_SAMPLE:
        return (
            f"⚠️ العيّنة صغيرة ({st.n} إشارة محسومة، والحد الأدنى {MIN_SAMPLE}). "
            "لا يمكن استنتاج أي حافة بعد — أي رقم موجب هنا قد يكون حظاً أو بيتا على الذهب."
        )
    edge = st.edge
    if edge is None:
        return "⚠️ لا توجد سيطرة كافية للمقارنة — العدد وحده لا يكفي للحكم."
    if edge > 0.15:
        return (
            f"✅ AYZR يتفوّق على العشوائي بفارق {edge:+.2f}R — إشارة تستحق المتابعة، "
            "لكن شهراً واحداً ونظام سوقي واحد لا يثبتان حافة."
        )
    if edge < -0.15:
        return f"❌ AYZR أسوأ من العشوائي بفارق {edge:+.2f}R — لا حافة هنا."
    return f"⚖️ AYZR ≈ العشوائي (فارق {edge:+.2f}R) — بيتا على الذهب بلغة أحلى، لا حافة توقيت."


def _fold(rows: list[dict]) -> list[dict]:
    """Fold resolution rows into their signals, in memory.

    The journal is append-only on disk for crash-safety; folding happens here so
    the report is always reading the current truth without a rewrite step.
    """
    signals: dict[str, dict] = {}
    for row in rows:
        if row.get("kind") == "signal":
            signals[_key(row)] = dict(row)
        elif row.get("kind") == "resolution":
            target = signals.get(row.get("key") or "")
            if target is not None:
                target.update(
                    {
                        "outcome": row.get("outcome"),
                        "r_multiple": row.get("r_multiple"),
                        "bars_held": row.get("bars_held"),
                        "resolved_at": row.get("resolved_at"),
                        "control": row.get("control"),
                    }
                )
    return list(signals.values())


def _key(rec: dict) -> str:
    return f"{rec.get('logged_at')}:{rec.get('direction')}"


def _start_of_day(now: float | None = None) -> int:
    """UTC midnight for ``now``, matching the journal's UTC timestamps."""
    now = now if now is not None else time.time()
    tm = time.gmtime(now)
    return calendar.timegm((tm.tm_year, tm.tm_mon, tm.tm_mday, 0, 0, 0, 0, 0, 0))


@dataclass
class Report:
    today: Stats = field(default_factory=Stats)
    total: Stats = field(default_factory=Stats)
    open_now: int = 0
    no_trade_today: int = 0
    no_trade_total: int = 0


def build_report(settings: AyzrSettings, now: float | None = None) -> Report:
    """The full picture: today, all time, in-flight, and no-trade calls."""
    start = _start_of_day(now)
    return Report(
        today=compute(settings, since=start),
        total=compute(settings),
        open_now=open_count(settings),
        no_trade_today=no_trade_count(settings, since=start),
        no_trade_total=no_trade_count(settings),
    )
