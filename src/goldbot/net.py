"""One guarded door for every outbound HTTP request.

Every module that talks to the network used to call ``urllib.request.urlopen``
directly. That is fine while the URL is a literal in this source tree, but the
URL is sometimes assembled from configuration or from a provider's response, and
``urlopen`` will happily open ``file://`` or any other scheme it understands.
The scheme is checked here once, so no call site has to remember to.

The check is deliberately permissive about ``http`` as well as ``https``: a
platform may hand the service a plain-``http`` public URL for the self-ping, and
refusing it would silently disable the keep-alive. What is refused is every
scheme that is *not* web traffic.
"""

from __future__ import annotations

import urllib.request
from typing import Any

ALLOWED_SCHEMES = frozenset({"http", "https"})


class UnsafeURL(ValueError):
    """A URL that is not plain web traffic was refused."""


def _scheme_of(target: Any) -> str:
    if isinstance(target, urllib.request.Request):
        target = target.full_url
    return str(target).split(":", 1)[0].lower() if ":" in str(target) else ""


def open_url(target: Any, timeout: float) -> Any:
    """``urlopen`` for http(s) only, as a context manager.

    Accepts a URL string or a prepared :class:`urllib.request.Request`, so call
    sites keep the shape they had. Raises :class:`UnsafeURL` — never a silent
    downgrade — when the scheme is anything but http or https.
    """
    scheme = _scheme_of(target)
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURL(f"refused non-web scheme {scheme or '(none)'!r}")
    # The scheme is checked immediately above. This is the single place the
    # check exists, so no call site can forget it.
    return urllib.request.urlopen(target, timeout=timeout)  # nosec B310
