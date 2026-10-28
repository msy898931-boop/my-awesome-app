"""Configuration, loaded from the environment once.

Every tunable lives here so the rest of the package never reads ``os.environ``
directly. Defaults are chosen to work with **no keys at all**: the data
providers are keyless, and the AI provider degrades to a deterministic read
when no key is present.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def load_dotenv(path: Path | None = None) -> None:
    """Load a ``.env`` file into ``os.environ``, without overriding real vars.

    The package has no third-party dependency, so this is a minimal parser:
    ``KEY=value`` per line, ``#`` comments and blank lines skipped, optional
    surrounding quotes stripped. Real environment variables always win, so a
    shell-exported secret is never clobbered by a stale file.
    """
    if path is None:
        path = Path(__file__).resolve().parents[2] / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def _timeframes() -> tuple[str, ...]:
    """The structural frames, in canonical order, filtered by the allow-list."""
    canonical = ("M1", "M5", "M15", "M30", "H1", "H4", "D1")
    raw = os.environ.get("GOLDBOT_TIMEFRAMES", "").strip()
    if not raw:
        return ("M5", "M15", "M30", "H1", "H4")
    wanted = {tok.strip().upper() for tok in raw.split(",") if tok.strip()}
    chosen = tuple(tf for tf in canonical if tf in wanted)
    return chosen or ("M5", "M15", "M30", "H1", "H4")


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings."""

    symbol: str = field(default_factory=lambda: os.environ.get("GOLDBOT_SYMBOL", "XAUUSD"))
    timeframes: tuple[str, ...] = field(default_factory=_timeframes)
    bars: int = field(default_factory=lambda: _int("GOLDBOT_BARS", 220))

    min_rr: float = field(default_factory=lambda: _float("GOLDBOT_MIN_RR", 2.0))
    poll_seconds: float = field(default_factory=lambda: _float("GOLDBOT_POLL_SECONDS", 60.0))

    telegram_token: str = field(default_factory=lambda: os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat: str = field(default_factory=lambda: os.environ.get("TELEGRAM_CHAT_ID", ""))

    groq_key: str = field(default_factory=lambda: os.environ.get("GROQ_API_KEY", ""))
    groq_model: str = field(
        default_factory=lambda: os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
    )
    # Groq meters each model separately, so a spent daily quota on one model
    # does not block the others. The chain is tried in order and the first
    # model that answers wins.
    groq_fallbacks: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            m.strip()
            for m in os.environ.get(
                "GROQ_FALLBACK_MODELS", "openai/gpt-oss-20b,qwen/qwen3.8-27b"
            ).split(",")
            if m.strip()
        )
    )
    # Reasoning models spend tokens thinking before they emit content, so the
    # budget has to cover both. Too small a cap returns an empty message.
    groq_max_tokens: int = field(
        default_factory=lambda: int(os.environ.get("GROQ_MAX_TOKENS", "3000"))
    )

    # Kraken (PAXG) leads: it is spot-linked gold, so it sits far closer to the
    # MT5 XAUUSD price than COMEX futures, which carry a contango premium.
    enable_yahoo: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_YAHOO", False))
    enable_kraken: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_KRAKEN", True))
    enable_binance: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_BINANCE", True))
    enable_goldapi: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_GOLDAPI", True))

    enable_bot: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_BOT", True))
    # The archive accumulates candles across runs; free sources only ever hand
    # back about a week of intraday, so this is the only way to build a sample
    # worth backtesting on.
    enable_archive: bool = field(default_factory=lambda: _flag("GOLDBOT_ENABLE_ARCHIVE", True))
    archive_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("GOLDBOT_ARCHIVE_DIR", ".goldbot/archive"))
    )

    @property
    def ai_enabled(self) -> bool:
        """Whether an AI provider is configured. Absent a key, the engine still runs."""
        return bool(self.groq_key.strip())


def load_settings() -> Settings:
    """Read the environment once and return settings. Callers may inject their own."""
    load_dotenv()
    return Settings()
