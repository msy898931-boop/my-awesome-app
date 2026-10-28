"""Macro context for gold: the dollar and the real yield, both keyless.

Why this exists. A five-year study of this project's own setups found the
strategy is **beta to gold, not skill** — a buy wins when the metal rises and
loses when it falls, in every frame and both directions. Measured against the
macro series below, that fact has a face: gold's level correlation with the
broad dollar is about **-0.6**. The bot was already reading the dollar without
naming it.

What this module is **not**. It is not a signal, and it does not change the
direction a plan chose. Two tests, run before any of this was written, are the
reason:

  * The relationship is **contemporaneous, not leading**. EURUSD's move one bar
    earlier correlates with gold's move now at **+0.004** daily and **-0.045**
    intraday — i.e. nothing. There is no lag to trade.
  * Gating gold momentum on dollar agreement made the result **worse**, not
    better (5-day lookback: mean +0.020% per bar alone versus -0.017% with the
    filter).

So the honest use is context: tell the trader which way the macro wind blows,
and say plainly when a plan is fighting it. An advisory that names the risk it
cannot remove is worth more than one that pretends to remove it.

Sources, both free and keyless:

  * **FRED** serves a plain CSV at ``fredgraph.csv?id=<SERIES>`` with no key and
    no sign-up. ``cosd`` bounds the range, because a full-history pull is
    megabytes and only the recent end is ever read.
  * **EURUSD** from the same Binance market-data host already used for candles,
    so it is live, deep, and needs no new dependency or credential.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..net import open_url

USER_AGENT = "gold-smc-ict-bot/0.1"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}&cosd={start}"

# The series that actually move gold. DFII10 is the real (inflation-adjusted)
# yield, the textbook driver; DTWEXBGS is the broad trade-weighted dollar.
FRED_SERIES = (
    ("DFII10", "real_yield_10y"),
    ("DTWEXBGS", "broad_dollar"),
    ("T10YIE", "inflation_expectation_10y"),
)

# The live dollar proxy. EURUSD is the deepest dollar pair, so a rising euro is
# a falling dollar and a tailwind for gold.
FX_PAIR = "EURUSDT"
FX_KLINES = "https://data-api.binance.vision/api/v3/klines"

# 400 calendar days is ample for a five-observation change and keeps the CSV
# small enough to fetch quickly; the full history is not needed and times out.
FRED_LOOKBACK_DAYS = 400
# A daily series does not change within the day, so re-fetching it every poll
# would be pure waste. The FX leg is live and gets a shorter window.
FRED_TTL = 6 * 3600.0
FX_TTL = 300.0
NEGATIVE_TTL = 300.0

# Observations back for the "trend" read. Business-daily FRED series are
# lagged by up to a week, so a short window would read the lag, not the market.
TREND_OBS = 5

# Moves smaller than these are noise, not a direction. Percent for prices,
# percentage points for a yield. Without a floor every wiggle would be called a
# bias and the word would stop meaning anything.
FX_MOVE_PCT = 0.15
DOLLAR_MOVE_PCT = 0.30
REAL_YIELD_MOVE_PP = 0.10

# Tailwind/headwind, in the direction that matters for gold.
TAILWIND = "tailwind"
HEADWIND = "headwind"
NEUTRAL = "neutral"
UNKNOWN = "unknown"
MIXED = "mixed"


@dataclass
class MacroContext:
    """The macro backdrop, as numbers and as one honest word."""

    generated_at: int = 0
    fx_pair: str = ""
    fx_change_1d_pct: float | None = None
    fx_change_5d_pct: float | None = None
    real_yield_10y: float | None = None
    real_yield_change: float | None = None
    broad_dollar: float | None = None
    broad_dollar_change: float | None = None
    inflation_expectation_10y: float | None = None
    inflation_change: float | None = None
    bias: str = UNKNOWN
    votes: dict = field(default_factory=dict)
    as_of: dict = field(default_factory=dict)
    checked: bool = False
    stale: bool = False
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "generated_at": self.generated_at,
            "fx_pair": self.fx_pair,
            "fx_change_1d_pct": self.fx_change_1d_pct,
            "fx_change_5d_pct": self.fx_change_5d_pct,
            "real_yield_10y": self.real_yield_10y,
            "real_yield_change": self.real_yield_change,
            "broad_dollar": self.broad_dollar,
            "broad_dollar_change": self.broad_dollar_change,
            "inflation_expectation_10y": self.inflation_expectation_10y,
            "inflation_change": self.inflation_change,
            "bias": self.bias,
            "votes": dict(self.votes),
            "as_of": dict(self.as_of),
            "checked": self.checked,
            "stale": self.stale,
            "error": self.error,
        }


def _vote_for_dollar(change_pct: float | None, floor: float) -> str:
    """A dollar move as a gold vote. A stronger dollar pressures gold.

    The sign is inverted here and nowhere else, so the inversion is stated once
    rather than re-derived at each call site.
    """
    if change_pct is None:
        return NEUTRAL
    if change_pct > floor:
        return HEADWIND
    if change_pct < -floor:
        return TAILWIND
    return NEUTRAL


def _vote_for_euro(change_pct: float | None, floor: float) -> str:
    """EURUSD is the dollar inverted: a rising euro is gold-supportive."""
    if change_pct is None:
        return NEUTRAL
    if change_pct > floor:
        return TAILWIND
    if change_pct < -floor:
        return HEADWIND
    return NEUTRAL


def _vote_for_real_yield(change_pp: float | None, floor: float) -> str:
    """A rising real yield raises gold's opportunity cost, so it pressures it."""
    if change_pp is None:
        return NEUTRAL
    if change_pp > floor:
        return HEADWIND
    if change_pp < -floor:
        return TAILWIND
    return NEUTRAL


def combine_votes(votes: dict) -> str:
    """Majority of the non-neutral votes, or a stated non-answer.

    Three independent reads vote. No non-neutral vote means the macro is quiet
    and the word is ``unknown``, not ``neutral`` — "nothing to say" and "no net
    lean" are different claims. An even split is ``mixed``, said out loud
    rather than resolved by picking a side.
    """
    cast = [v for v in votes.values() if v in (TAILWIND, HEADWIND)]
    if not cast:
        return UNKNOWN
    tail = cast.count(TAILWIND)
    head = cast.count(HEADWIND)
    if tail > head:
        return TAILWIND
    if head > tail:
        return HEADWIND
    return MIXED


def alignment(bias: str, direction: str) -> str:
    """Whether a plan's direction fights the macro wind.

    ``"with"``, ``"against"`` or ``""`` when there is nothing to compare. A buy
    into dollar strength is the case this exists to name, because it is exactly
    the beta that produced the losing book.
    """
    if direction not in {"buy", "sell"}:
        return ""
    if bias == TAILWIND:
        return "with" if direction == "buy" else "against"
    if bias == HEADWIND:
        return "with" if direction == "sell" else "against"
    return ""


def parse_fred_csv(text: str) -> list[tuple[int, float]]:
    """FRED CSV rows as ``(day, value)``, oldest first, holidays dropped.

    FRED writes ``.`` for a day with no observation (a holiday), and those rows
    must be skipped rather than parsed as zero — a zero would read as a violent
    move in the wrong direction.
    """
    rows: list[tuple[int, float]] = []
    for line in text.splitlines()[1:]:
        parts = line.split(",")
        if len(parts) < 2:
            continue
        value = parts[1].strip()
        if value in ("", "."):
            continue
        try:
            day = _day_of(parts[0].strip())
            rows.append((day, float(value)))
        except ValueError:
            continue
    rows.sort(key=lambda r: r[0])
    return rows


def _day_of(text: str) -> int:
    """A ``YYYY-MM-DD`` date as a whole day number, so rows are comparable."""
    from datetime import date

    y, m, d = (int(part) for part in text.split("-"))
    return date(y, m, d).toordinal()


def _change(rows: list[tuple[int, float]], obs: int = TREND_OBS) -> float | None:
    """Change over ``obs`` observations, or ``None`` when there is too little."""
    if len(rows) < obs + 1:
        return None
    return round(rows[-1][1] - rows[-1 - obs][1], 4)


class MacroFeed:
    """Fetches the macro backdrop, best-effort, with the same honesty rules.

    "Best-effort" must not mean "silently quiet", exactly as for the calendar:
    ``ok`` and ``error`` report the last attempt, a partial failure is reported
    per leg, and a failed refresh keeps the previous good numbers (marked
    ``stale``) instead of replacing them with silence. The two legs carry
    different TTLs because one is a daily series and the other is live.
    """

    def __init__(
        self,
        fred_series=FRED_SERIES,
        fx_pair: str = FX_PAIR,
        timeout: float = 15.0,
    ) -> None:
        self.fred_series = tuple(fred_series)
        self.fx_pair = fx_pair
        self.timeout = timeout
        self._fred: tuple[float, dict, str, dict] = (0.0, {}, "", {})
        self._fx: tuple[float, dict, str] = (0.0, {}, "")
        # Explicit retry gates, exactly as the calendar and RSS feeds use. A
        # failed fetch must not be retried on the next call: hammering a source
        # that just refused is what keeps it refusing.
        self._retry_fred = 0.0
        self._retry_fx = 0.0
        self._ok = False
        self._error = ""

    @property
    def ok(self) -> bool:
        """Whether the most recent attempt reached every leg it asked for."""
        return self._ok

    @property
    def error(self) -> str:
        """Why the last attempt failed, or an empty string when it did not."""
        return self._error

    def context(
        self, now: float | None = None, ttl_fred: float = FRED_TTL, ttl_fx: float = FX_TTL
    ) -> MacroContext:
        """Assemble the backdrop from whichever legs are fresh enough."""
        now = now if now is not None else time.time()
        fred_values, fred_error, fred_as_of = self._fred_leg(now, ttl_fred)
        fx_values, fx_error = self._fx_leg(now, ttl_fx)

        errors = [e for e in (fred_error, fx_error) if e]
        self._ok = not errors
        self._error = "; ".join(errors)

        real = fred_values.get("real_yield_10y")
        dollar = fred_values.get("broad_dollar")
        infl = fred_values.get("inflation_expectation_10y")

        votes = {
            "dollar_live": _vote_for_euro(fx_values.get("change_1d_pct"), FX_MOVE_PCT),
            "dollar_trend": _vote_for_dollar(dollar[1] if dollar else None, DOLLAR_MOVE_PCT),
            "real_yield": _vote_for_real_yield(real[1] if real else None, REAL_YIELD_MOVE_PP),
        }

        return MacroContext(
            generated_at=int(now),
            fx_pair=self.fx_pair,
            fx_change_1d_pct=fx_values.get("change_1d_pct"),
            fx_change_5d_pct=fx_values.get("change_5d_pct"),
            real_yield_10y=real[0] if real else None,
            real_yield_change=real[1] if real else None,
            broad_dollar=dollar[0] if dollar else None,
            broad_dollar_change=dollar[1] if dollar else None,
            inflation_expectation_10y=infl[0] if infl else None,
            inflation_change=infl[1] if infl else None,
            bias=combine_votes(votes),
            votes=votes,
            as_of=fred_as_of,
            checked=bool(fred_values or fx_values),
            stale=bool(errors) and bool(fred_values or fx_values),
            error=self._error,
        )

    def _fred_leg(self, now: float, ttl: float) -> tuple[dict, str, dict]:
        """The FRED series, cached on their own (long) TTL."""
        cached_at, values, error, as_of = self._fred
        # Only an *error-free* cache counts as fresh. Serving kept-but-stale
        # numbers as clean is exactly the trap the calendar had: a rate-limited
        # read would look like a calm one.
        if values and not error and now - cached_at < ttl:
            return values, "", as_of
        if error and now < self._retry_fred:
            return values, error, as_of

        out: dict[str, tuple[float, float | None]] = {}
        observed: dict[str, int] = {}
        failures: list[str] = []
        start = time.strftime("%Y-%m-%d", time.gmtime(now - FRED_LOOKBACK_DAYS * 86400))
        for series, name in self.fred_series:
            rows, problem = self._fetch_fred(series, start)
            if problem:
                failures.append(f"{series}: {problem}")
                continue
            if not rows:
                failures.append(f"{series}: empty")
                continue
            out[name] = (rows[-1][1], _change(rows))
            observed[name] = rows[-1][0]

        # A leg that failed keeps its previous number and is reported; only a
        # total failure keeps the whole previous read. Either way the retry gate
        # is set and the (possibly empty) result stored, so the next call backs
        # off instead of re-hammering.
        if failures:
            self._retry_fred = now + NEGATIVE_TTL
        merged = {**values, **out}
        merged_as_of = {**as_of, **observed}
        self._fred = (now, merged, "; ".join(failures), merged_as_of)
        return merged, "; ".join(failures), merged_as_of

    def _fetch_fred(self, series: str, start: str) -> tuple[list[tuple[int, float]], str]:
        url = FRED_CSV.format(series=series, start=start)
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with open_url(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return [], f"HTTP {resp.status}"
                text = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return [], f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return [], type(exc).__name__
        return parse_fred_csv(text), ""

    def _fx_leg(self, now: float, ttl: float) -> tuple[dict, str]:
        """EURUSD, cached on a short TTL because it is live."""
        cached_at, values, error = self._fx
        if values and not error and now - cached_at < ttl:
            return values, ""
        if error and now < self._retry_fx:
            return values, error

        fetched, problem = self._fetch_fx()
        if problem:
            self._retry_fx = now + NEGATIVE_TTL
            return values, problem
        self._fx = (now, fetched, "")
        return fetched, ""

    def _fetch_fx(self) -> tuple[dict, str]:
        url = f"{FX_KLINES}?symbol={self.fx_pair}&interval=1d&limit=12"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with open_url(req, timeout=self.timeout) as resp:
                if resp.status != 200:
                    return {}, f"HTTP {resp.status}"
                rows = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            return {}, f"HTTP {exc.code}"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            return {}, type(exc).__name__
        if not isinstance(rows, list) or len(rows) < 6:
            return {}, "unexpected payload"

        closes: list[float] = []
        for row in rows:
            value = _close(row)
            if value is not None:
                closes.append(value)
        if len(closes) < 6:
            return {}, "unexpected payload"
        last, prev, five_back = closes[-1], closes[-2], closes[-6]
        return {
            "close": round(last, 5),
            "change_1d_pct": round((last - prev) / prev * 100.0, 4) if prev else None,
            "change_5d_pct": round((last - five_back) / five_back * 100.0, 4)
            if five_back
            else None,
        }, ""


def _close(row) -> float | None:
    """The close of one kline: ``[openTime, o, h, l, close, volume, ...]``."""
    try:
        return float(row[4])
    except (TypeError, ValueError, IndexError):
        return None


# One feed per process, so the cache and the back-off actually work — building a
# fresh feed per call is what turns a single rate-limit into a permanent one.
_FEED: MacroFeed | None = None


def _feed() -> MacroFeed:
    global _FEED
    if _FEED is None:
        _FEED = MacroFeed()
    return _FEED


def macro_context(now: float | None = None) -> dict:
    """The macro backdrop as a JSON-ready dict. Never raises."""
    try:
        return _feed().context(now).as_dict()
    except Exception as exc:  # a context layer must never take a read down
        return MacroContext(
            generated_at=int(now if now is not None else time.time()),
            checked=False,
            error=f"{type(exc).__name__}: {exc}",
        ).as_dict()
