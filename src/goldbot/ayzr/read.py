"""AYZR's market read: price structure, news, macro, and headlines, in one pass.

This module is where AYZR touches the outside world, and it does so through the
project's existing, already-tested layers rather than new ones:

  * market candles  — ``goldbot.data.feed`` (Kraken/Binance, keyless);
  * structure facts — ``goldbot.smc.engine.build`` (the SMC/ICT arithmetic);
  * news + macro    — ``goldbot.news.window.digest`` (calendar, RSS, dollar/yield).

Everything here is **read-only**. AYZR never writes into the main bot's archive,
state, or calendar cache; it only reads them if a caller has them to hand. That
keeps the two engines independently debuggable — a fault in AYZR cannot corrupt
the bot that is already trusted.

The output is a plain dict: the fact sheet AYZR reasons over, plus the news
picture, plus the spot cross-check. It is deliberately not the main bot's
``Result`` type, so a change to AYZR can never widen the main bot's surface.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ..config import Settings
from ..data.feed import build_feed
from ..news.window import digest as news_digest
from ..smc.engine import build as build_facts
from .config import AyzrSettings


@dataclass
class Read:
    """One AYZR market read, and the trail of how it was obtained."""

    generated_at: int = 0
    symbol: str = ""
    facts: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    spot: dict | None = None
    series: dict = field(default_factory=dict)  # tf -> last close + ATR, for context
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.facts.get("timeframes"))


def read_market(settings: AyzrSettings, feed=None) -> Read:
    """Build one read for AYZR.

    ``feed`` is injectable so the tests can drive a fixed tape without a socket;
    in production it is built from the shared provider list. The feed is the
    *only* shared object, and it is used read-only (no archive is attached, so
    nothing is written).
    """
    now = int(time.time())
    out = Read(generated_at=now, symbol=settings.symbol)

    feed = feed or _build_readonly_feed(settings)
    series_by_tf: dict = {}
    for tf in settings.context_tfs:
        try:
            series, report = feed.series(tf, settings.bars)
        except Exception as exc:  # a provider must never take AYZR down
            out.errors.append(f"{tf}: {type(exc).__name__}: {exc}")
            continue
        if series is None:
            out.errors.append(f"{tf}: لا بيانات ({report.attempts})")
            continue
        series_by_tf[tf] = series
        last = series.bars[-1] if series.bars else None
        out.series[tf] = {
            "close": last.close if last else None,
            "bars": len(series.bars),
            "source": series.source,
        }

    if not series_by_tf:
        out.errors.append("لا يوجد أي فريم متاح — تعذّر قراءة السوق")
        return out

    sheet = build_facts(series_by_tf, settings.symbol, now=now)
    out.facts = sheet.as_dict()

    primary = series_by_tf.get(settings.primary_tf) or next(iter(series_by_tf.values()))
    try:
        out.spot = feed.spot_check(primary)
    except Exception as exc:
        out.errors.append(f"spot: {type(exc).__name__}: {exc}")

    try:
        out.news = news_digest(now, with_macro=True)
    except Exception as exc:
        out.errors.append(f"news: {type(exc).__name__}: {exc}")

    return out


def _build_readonly_feed(settings: AyzrSettings):
    """A feed over the shared providers, with **no** archive attached.

    No archive is the point: attaching one would let an AYZR read write into the
    main bot's candle store, which is exactly the kind of hidden coupling this
    engine is built to avoid. AYZR may read the tape; it may not touch the bot's
    files to do it.
    """
    shared = Settings(
        symbol=settings.symbol,
        timeframes=settings.context_tfs,
        bars=settings.bars,
        enable_archive=False,
    )
    return build_feed(shared)
