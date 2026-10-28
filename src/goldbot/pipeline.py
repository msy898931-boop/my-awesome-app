"""The pipeline: one call that goes from the market to a decision.

Every surface (the bot, the CLI, the backtest) goes through this, so they can
never disagree about what the market is doing. The order is fixed and visible:

    data -> facts -> news -> plan -> AI -> result

Each stage degrades rather than raising: no data means no result, no news means
an empty digest, no AI key means a deterministic summary. The pipeline's job is
to always return a coherent document, and to record what it could not do.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Optional

from .ai.analyst import Analyst
from .config import Settings
from .data.feed import MarketFeed, build_feed
from .news.window import digest as news_digest
from .smc.engine import build as build_facts
from .strategy.plan import build_plan


@dataclass
class Result:
    """The complete output of one analysis run, ready to render or store."""

    generated_at: int
    symbol: str
    facts: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    plan: dict = field(default_factory=dict)
    analysis: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    feed_notes: list = field(default_factory=list)
    spot: Optional[dict] = None
    errors: list = field(default_factory=list)

    @property
    def actionable(self) -> bool:
        return self.plan.get("direction") in {"buy", "sell"}

    def as_dict(self) -> dict:
        out = asdict(self)
        out["actionable"] = self.actionable
        return out


class Pipeline:
    """Wires the stages together. Construct once, call ``run`` on a schedule."""

    def __init__(self, settings: Settings, feed: Optional[MarketFeed] = None) -> None:
        self.settings = settings
        self.feed = feed or build_feed(settings)
        self.analyst = Analyst(settings)
        # A command that repeats within the window reuses the last read. The
        # market does not move in seconds, and a trader pressing /حالة twice
        # should not pay two network round-trips plus an AI call for it.
        self._cache: dict[tuple[bool, bool], tuple[float, Result]] = {}

    def run(self, with_ai: bool = True, with_news: bool = True,
            max_age: Optional[float] = None) -> Result:
        key = (with_ai, with_news)
        age = self.settings.cache_seconds if max_age is None else max_age
        cached = self._cache.get(key)
        if cached and age > 0 and (time.time() - cached[0]) < age:
            return cached[1]

        result = self._read(with_ai=with_ai, with_news=with_news)
        self._cache[key] = (time.time(), result)
        return result

    def _read(self, with_ai: bool, with_news: bool) -> Result:
        now = int(time.time())
        result = Result(generated_at=now, symbol=self.settings.symbol)

        series_by_tf = {}
        for tf in self.settings.timeframes:
            series, report = self.feed.series(tf, self.settings.bars)
            if series is None:
                result.errors.append(f"{tf}: لا بيانات ({report.attempts})")
                result.feed_notes.append({"timeframe": tf, "attempts": report.attempts})
                continue
            series_by_tf[tf] = series
            result.feed_notes.append({"timeframe": tf, "chosen": report.chosen,
                                      "attempts": report.attempts})

        if not series_by_tf:
            result.errors.append("لا يوجد أي فريم متاح — تعذّر التحليل")
            return result

        sheet = build_facts(series_by_tf, self.settings.symbol, now=now)
        result.facts = sheet.as_dict()
        result.sources = sheet.sources

        primary = series_by_tf.get("H1") or next(iter(series_by_tf.values()))
        result.spot = self.feed.spot_check(primary)

        if with_news:
            try:
                result.news = news_digest(now)
            except Exception as exc:
                result.errors.append(f"news: {type(exc).__name__}: {exc}")

        plan = build_plan(result.facts, self.settings.min_rr)
        result.plan = plan.as_dict()

        if with_ai:
            analysis = self.analyst.analyse(result.facts, result.news or None,
                                            self.settings.min_rr)
            result.analysis = analysis.as_dict()

        return result


def run_once(settings: Settings, with_ai: bool = True, with_news: bool = True) -> Result:
    """Convenience for a single run with fresh defaults."""
    return Pipeline(settings).run(with_ai=with_ai, with_news=with_news)
