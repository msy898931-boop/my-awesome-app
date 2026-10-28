"""The market feed: picks the best source per timeframe and explains the choice.

The selection rule is deliberately boring and inspectable:

  1. Ask every enabled provider for the requested frame, in priority order.
  2. Drop any reply that errors or returns fewer bars than the minimum needed
     to read structure.
  3. Among the survivors, prefer the one whose newest bar is *freshest*. A
     stale-but-valid series is worse than a slightly-off-but-live one, because
     structure read off a frozen tape describes a market that has already moved.
  4. Tie-break on priority, so behaviour is deterministic.

Every read carries its provenance — which provider served it and how many bars
— so a plan can always be traced back to the tape it came from.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Settings
from ..models import Series
from .archive import Archive
from .base import Provider, ProviderError
from .binance import BinanceProvider
from .goldapi import GoldApiProvider
from .kraken import KrakenProvider
from .yahoo import YahooProvider

# Below this, swing detection is noise; the frame is refused outright.
MIN_BARS = 30


@dataclass
class FeedReport:
    """What happened on the way to a series. Kept so failures are visible."""

    timeframe: str
    chosen: str = ""
    attempts: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.chosen)

    def note(self, provider: str, outcome: str) -> None:
        self.attempts.append((provider, outcome))


class MarketFeed:
    """Fetches bars from the first healthy provider, with a written trail.

    When an :class:`Archive` is attached, every successful read is folded into
    it. Free intraday sources cap out near 720 bars, so the archive is how the
    bot accumulates enough history to backtest over a real sample instead of a
    single week.
    """

    def __init__(
        self,
        providers: list[Provider],
        min_bars: int = MIN_BARS,
        archive: Archive | None = None,
    ) -> None:
        self.providers = providers
        self.min_bars = min_bars
        self.archive = archive

    def series(
        self, timeframe: str, bars: int, prefer_depth: bool = False
    ) -> tuple[Series | None, FeedReport]:
        """Pick a series for ``timeframe``.

        Live trading wants the freshest tape, because structure read off a
        frozen series describes a market that has already moved. A backtest
        wants the opposite: the longest history, since its whole purpose is a
        sample big enough to judge. ``prefer_depth`` switches between the two,
        and the choice is recorded in the report rather than left implicit.
        """
        report = FeedReport(timeframe=timeframe.upper())
        candidates: list[Series] = []
        for provider in self.providers:
            try:
                series = provider.fetch(timeframe, bars)
            except ProviderError as exc:
                report.note(provider.name, f"error: {exc}")
                continue
            except Exception as exc:  # a provider must never take the feed down
                report.note(provider.name, f"crash: {type(exc).__name__}: {exc}")
                continue
            if len(series) < self.min_bars:
                report.note(provider.name, f"thin: {len(series)} bars")
                continue
            report.note(provider.name, f"ok: {len(series)} bars")
            candidates.append(series)

        if self.archive is not None:
            for series in candidates:
                try:
                    added = self.archive.merge(timeframe, series)
                    if added:
                        report.note("archive", f"+{added} bars")
                except OSError as exc:
                    report.note("archive", f"write failed: {exc}")
            # For a study, the archive is the best candidate there is: it is a
            # merge of every provider ever seen, so it is the longest and needs
            # no network. Offering it here makes repeat backtests fast and
            # complete rather than re-paging years on every run.
            if prefer_depth:
                stored = self.archive.series(timeframe, bars)
                if stored is not None and len(stored) >= self.min_bars:
                    candidates.append(stored)
                    report.note("archive", f"candidate {len(stored)} bars")

        if not candidates:
            # Fall back to the archive when every live provider is unreachable.
            if self.archive is not None:
                stored = self.archive.series(timeframe, bars)
                if stored is not None:
                    report.note("archive", f"served {len(stored)} bars")
                    report.chosen = stored.source
                    return stored, report
            return None, report

        if prefer_depth:
            # Longest history wins; ties fall to the earlier (higher-priority)
            # provider, so the outcome stays deterministic.
            best = max(candidates, key=lambda s: len(s.bars))
        else:
            # Freshest tape wins; ties fall to the earlier (higher-priority)
            # provider, so the outcome stays deterministic.
            best = max(candidates, key=lambda s: s.bars[-1].time)
        report.chosen = best.source
        return best, report

    def spot_check(self, series: Series) -> dict | None:
        """Compare the newest close against keyless spot quotes.

        Returns ``None`` when no spot source answers, so an offline check is
        silent rather than a false alarm. When quotes exist, the divergence in
        percent is reported and a ``trusted`` flag is set: a series whose close
        is more than 3% from spot is flagged, never silently used.
        """
        quotes: dict[str, float] = {}
        for provider in self.providers:
            try:
                price = provider.spot()
            except Exception:
                price = None
            if price:
                quotes[provider.name] = price
        if not quotes or not series.bars:
            return None
        close = series.bars[-1].close
        if close == 0:
            return None
        worst = max(abs(price - close) / close for price in quotes.values())
        return {
            "close": close,
            "quotes": quotes,
            "max_divergence_pct": round(worst * 100, 3),
            "trusted": worst <= 0.03,
        }

    def staleness_minutes(self, series: Series, timeframe: str) -> float | None:
        """How far behind the wall clock the newest bar's open is, in minutes."""
        if not series.bars:
            return None
        minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240, "D1": 1440}
        step = minutes.get(timeframe.upper(), 60)
        return max(0.0, (time.time() - series.bars[-1].time) / 60.0 - step)


def build_feed(settings: Settings) -> MarketFeed:
    """Assemble the provider list from settings, priority order first.

    Kraken (PAXG) leads: it is tokenised allocated gold trading spot-like, so
    it tracks the MT5 XAUUSD quote far more closely than COMEX futures, which
    carry a contango premium. gold-api follows and only ever contributes to the
    spot cross-check, since it serves no candles. Yahoo is last and off by
    default — its futures premium is a known bias, and it rate-limits hard.
    """
    providers: list[Provider] = []
    if settings.enable_kraken:
        providers.append(KrakenProvider())
    # Binance serves the same tokenised gold as Kraken but pages back years, so
    # it is what makes an honest backtest possible. It is also folded into the
    # archive on every run, which deepens the local store immediately.
    if settings.enable_binance:
        providers.append(BinanceProvider())
    if settings.enable_goldapi:
        providers.append(GoldApiProvider())
    if settings.enable_yahoo:
        providers.append(YahooProvider())
    archive = Archive(settings.archive_dir) if settings.enable_archive else None
    return MarketFeed(providers, archive=archive)
