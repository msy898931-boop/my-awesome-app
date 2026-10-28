"""A local candle archive that grows a little every time the bot runs.

Every free intraday source caps out at roughly 720 bars — about a week of M15 —
and the deeper endpoints are rate-limited or blocked. That is not enough to
backtest an edge honestly, and no amount of retrying changes it.

The fix is patience: each run appends what it just fetched to a small on-disk
store keyed by timeframe. Bars are de-duplicated by open time and kept sorted,
so repeated runs converge on the real series rather than accumulating garbage.
After a few weeks the archive holds far more history than any single request
could return, and the backtest can finally be run over a meaningful sample.

The store is plain JSON per timeframe. It is small, human-inspectable, and safe
to delete: a missing archive simply starts over.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from ..models import Bar, Series


class Archive:
    """Append-only, de-duplicated candle store on disk."""

    def __init__(self, root: Path, max_bars: int = 200_000) -> None:
        self.root = Path(root)
        self.max_bars = max_bars

    def _path(self, timeframe: str) -> Path:
        return self.root / f"{timeframe.upper()}.json"

    def load(self, timeframe: str) -> list[Bar]:
        path = self._path(timeframe)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        bars = []
        for row in raw if isinstance(raw, list) else []:
            bar = Bar.from_dict(row)
            if bar is not None and bar.is_sane():
                bars.append(bar)
        return bars

    def merge(self, timeframe: str, incoming: Series) -> int:
        """Fold a freshly fetched series into the store.

        Returns how many *new* bars were added, which is the honest measure of
        whether a run actually bought any history.
        """
        existing = {b.time: b for b in self.load(timeframe)}
        before = len(existing)
        for bar in incoming.bars:
            if bar.is_sane():
                # Newer fetch wins: a provider may revise its most recent bar.
                existing[bar.time] = bar
        merged = [existing[t] for t in sorted(existing)]
        if len(merged) > self.max_bars:
            merged = merged[-self.max_bars :]
        self._write(timeframe, merged)
        return len(existing) - before

    def series(self, timeframe: str, bars: int) -> Series | None:
        stored = self.load(timeframe)
        if not stored:
            return None
        return Series(timeframe=timeframe.upper(), bars=stored[-bars:], source="archive")

    def coverage(self, timeframe: str) -> dict:
        stored = self.load(timeframe)
        if not stored:
            return {"bars": 0, "from": None, "to": None}
        return {"bars": len(stored), "from": stored[0].time, "to": stored[-1].time}

    def _write(self, timeframe: str, bars: list[Bar]) -> None:
        """Write atomically: a crash mid-write must not corrupt the store."""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(timeframe)
        payload = json.dumps([b.as_dict() for b in bars], ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=str(self.root), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, path)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
