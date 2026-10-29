"""AYZR — settings that belong to AYZR alone.

AYZR is a separate engine that lives beside the main bot rather than inside it.
It therefore carries its **own** data root, its **own** AI key and quota
counter, and its **own** model chain, so that running AYZR can never spend the
main bot's AI budget and can never read or write the main bot's state. The one
thing AYZR shares with the project is the market data layer, which is read-only.

Every field falls back to a sane default, so AYZR runs with nothing configured
beyond a key. When no key is set the engine still produces its deterministic
read — it simply cannot ask a model to weigh the picture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _flag(name: str, default: bool) -> bool:
    """A boolean env var, read the way the rest of the project reads them."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _data_root() -> Path:
    """AYZR's own folder, kept apart from the main bot's durable state.

    The main bot writes ``bot_state.json`` and ``archive/`` under the shared
    data root; AYZR writes only under ``<root>/ayzr/``, so the two cannot
    collide even when they share a persistent volume.
    """
    explicit = os.environ.get("AYZR_DATA_DIR", "").strip()
    if explicit:
        return Path(explicit)
    root = os.environ.get("GOLDBOT_DATA_DIR", "").strip()
    return (Path(root) / "ayzr") if root else (Path(".goldbot") / "ayzr")


@dataclass
class AyzrSettings:
    """Everything AYZR needs, and nothing it borrows from the main bot."""

    symbol: str = field(default_factory=lambda: os.environ.get("AYZR_SYMBOL", "XAUUSD"))

    # The frame AYZR signals on. Its own choice: M15 is the fastest frame that
    # still carries readable structure without being noise.
    primary_tf: str = field(default_factory=lambda: os.environ.get("AYZR_TF", "M15"))

    # The wider frames AYZR reads for context. Kept as its own tuple so a
    # change here never touches the main bot's `timeframes`.
    context_tfs: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            t.strip().upper()
            for t in os.environ.get("AYZR_CONTEXT_TFS", "M15,M30,H1,H4").split(",")
            if t.strip()
        )
    )

    bars: int = field(default_factory=lambda: _int("AYZR_BARS", 400))

    # AYZR's own minimum reward-to-risk. Deliberately its own knob: tuning AYZR
    # must not move the main bot's sensitivity.
    min_rr: float = field(default_factory=lambda: _float("AYZR_MIN_RR", 2.0))

    # How many bars a resting entry may wait before the idea is void, and how
    # long a filled trade may run before it is marked to market.
    fill_window: int = field(default_factory=lambda: _int("AYZR_FILL_WINDOW", 8))
    horizon: int = field(default_factory=lambda: _int("AYZR_HORIZON", 48))

    # ── the AI, on AYZR's own budget ──────────────────────────────────────
    # `AYZR_AI_KEY` is preferred so AYZR can be pointed at a second free key;
    # it falls back to the project key so AYZR works out of the box.
    ai_key: str = field(
        default_factory=lambda: (
            os.environ.get("AYZR_AI_KEY", "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
        )
    )
    ai_model: str = field(
        default_factory=lambda: os.environ.get("AYZR_AI_MODEL", "openai/gpt-oss-120b")
    )
    ai_fallbacks: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            m.strip()
            for m in os.environ.get("AYZR_AI_FALLBACKS", "openai/gpt-oss-20b").split(",")
            if m.strip()
        )
    )
    ai_max_tokens: int = field(default_factory=lambda: _int("AYZR_AI_MAX_TOKENS", 6000))

    # AYZR's *own* daily ceiling. The main bot's cap is 30; AYZR keeps its own
    # counter so a day of AYZR analysis cannot starve the main bot's cards.
    ai_daily_cap: int = field(default_factory=lambda: _int("AYZR_AI_DAILY_CAP", 24))

    # ── the shadow runner ─────────────────────────────────────────────────
    # Off by default. The one-month test is an experiment, not a behaviour the
    # bot gains silently: it starts only when AYZR_ENABLED is set, and the bot's
    # own loop is exactly as it was when it is not.
    enabled: bool = field(default_factory=lambda: _flag("AYZR_ENABLED", False))
    # Seconds between passes. 0 means "one bar of ``primary_tf``" — the sane
    # default, since a pass reads the tape the frame it signals on defines.
    interval_seconds: int = field(default_factory=lambda: _int("AYZR_INTERVAL_SECONDS", 0))

    data_dir: Path = field(default_factory=_data_root)

    @property
    def ai_enabled(self) -> bool:
        return bool(self.ai_key.strip())

    @property
    def log_path(self) -> Path:
        """The shadow journal: every signal AYZR produced, and its outcome."""
        return self.data_dir / "signals.jsonl"

    @property
    def ai_state_path(self) -> Path:
        """AYZR's private AI-quota counter, so its cap survives a restart."""
        return self.data_dir / "ai_state.json"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
