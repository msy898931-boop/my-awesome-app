"""Provider contract and shared HTTP helper.

Every provider is a plain object with ``name`` and ``fetch(timeframe, bars)``.
It returns a :class:`~goldbot.models.Series` or raises :class:`ProviderError`.
Nothing here caches, retries forever, or hides a failure: a dead feed must be
visible to the caller so it can fall through to the next provider.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..models import Bar, Series
from ..net import open_url

# Yahoo, Groq and several news hosts sit behind Cloudflare, which rejects a
# custom bot User-Agent with a 4xx. A browser-like UA is required to get JSON
# at all; the bot's identity is carried in the project's own docs instead.
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15.0


class ProviderError(RuntimeError):
    """A provider could not serve the request. Never fatal on its own."""


def http_get_json(url: str, timeout: float = DEFAULT_TIMEOUT) -> Any:
    """GET a URL and parse JSON, with a browser-ish UA.

    A non-200, a timeout, or malformed JSON all raise :class:`ProviderError`
    with the reason attached, so the feed can log *why* it moved on.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with open_url(req, timeout=timeout) as resp:
            if resp.status != 200:
                raise ProviderError(f"HTTP {resp.status}")
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise ProviderError(f"HTTP {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ProviderError(f"network: {exc}") from exc
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProviderError(f"bad JSON: {exc}") from exc


def clean_bars(bars: list[Bar]) -> list[Bar]:
    """Drop unusable bars, sort by time, and de-duplicate on open time.

    Providers occasionally emit a zero-price seed bar or a repeated stamp. Both
    would corrupt structure detection, so they are removed here rather than
    defended against everywhere downstream.
    """
    seen: set[int] = set()
    out: list[Bar] = []
    for bar in sorted(bars, key=lambda b: b.time):
        if bar.time in seen or not bar.is_sane():
            continue
        seen.add(bar.time)
        out.append(bar)
    return out


class Provider:
    """Base class: subclasses set ``name`` and implement ``fetch``."""

    name: str = "provider"
    # Whether this source trades 24/7 (crypto-gold) or ~24/5 (futures/spot).
    always_on: bool = False

    def fetch(self, timeframe: str, bars: int) -> Series:  # pragma: no cover - interface
        raise NotImplementedError

    def spot(self) -> float | None:  # pragma: no cover - optional
        """A latest price, when the provider can offer one cheaply."""
        return None
