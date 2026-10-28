"""The guarded door, and a bounded reader.

Two problems, one module.

**Scheme.** ``urllib.request.urlopen`` will happily open ``file://`` and any
other scheme it understands. That is fine while the URL is a literal, but the
URL is often assembled from configuration or from a provider's own response,
and then the scheme is attacker-influenced. ``guarded_url`` checks it in one
place, so no call site has to remember to — and no call site *can* forget.

**Size.** A response body is read into memory. Feeds are tens of kilobytes; a
hostile or broken endpoint can return gigabytes, and reading it to discover
that is the denial of service. ``read_capped`` stops at a ceiling.
"""

from __future__ import annotations

import urllib.request
from typing import Any

ALLOWED_SCHEMES = frozenset({"http", "https"})

# Bodies larger than this are refused before they are parsed. Well above any
# real feed or JSON payload, well below anything that hurts to hold in memory.
MAX_BODY_BYTES = 2_000_000


class UnsafeURL(ValueError):
    """A URL that is not plain web traffic was refused."""


class ResponseTooLarge(ValueError):
    """A response body exceeded the cap and was not read."""


def _scheme_of(target: Any) -> str:
    if isinstance(target, urllib.request.Request):
        target = target.full_url
    text = str(target)
    return text.split(":", 1)[0].lower() if ":" in text else ""


def guarded_url(target: Any, timeout: float) -> Any:
    """``urlopen`` for http(s) only, as a context manager.

    Accepts a URL string or a prepared :class:`urllib.request.Request`, so call
    sites keep the shape they had. Raises :class:`UnsafeURL` — never a silent
    downgrade — when the scheme is anything but http or https.
    """
    scheme = _scheme_of(target)
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"refused non-web scheme {scheme or '(none)'!r}")
    # The scheme is checked immediately above, and this is the single place the
    # check exists, so no call site can forget it.
    return urllib.request.urlopen(target, timeout=timeout)  # nosec B310


def read_capped(response: Any, limit: int = MAX_BODY_BYTES) -> bytes:
    """Read a response body, refusing anything over ``limit`` bytes.

    Reads one byte past the limit so an exactly-``limit`` body is accepted and a
    larger one is caught, rather than trusting a ``Content-Length`` header the
    sender controls.
    """
    body = response.read(limit + 1)
    if len(body) > limit:
        raise ResponseTooLarge(f"response exceeded {limit} bytes")
    return body


# The name GoldBot uses in its own source. Kept as an alias so the extracted
# pattern and the code it came from read the same.
open_url = guarded_url
