"""A cached leg with an honest freshness test and a retry gate that fires.

This is the module where two bugs were fixed, and both are worth stating
because the wrong version looks correct.

**Bug 1 — the gate that never fires.** The first version kept the old
``cached_at`` on a failed fetch and gated the retry on
``now - cached_at < NEGATIVE_TTL``. But ``cached_at`` was old, so that
expression was false immediately and the source was re-hammered on *every*
subsequent call. A back-off must compare against an **absolute deadline set at
the moment of failure** (``retry_at = now + NEGATIVE_TTL``), not against the
age of a stale value.

**Bug 2 — the stale value served as fresh.** The freshness test was "do we have
values and are they within the TTL". A value kept through a failure then passed
that test on the next call and was returned with no error — a rate-limited read
masquerading as a clean one, which is the exact class of bug this whole library
exists to prevent. The test must be ``values and not error and within ttl``.

A ``Leg`` is one independently-cached source. A source made of several legs
(e.g. a daily series and a live tick) gives each its own ``Leg``, because they
have different TTLs and fail independently.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")

# Back-off after a failed fetch. A source that answered "too many requests"
# should not be hit again immediately; this is what keeps a 429 from becoming
# permanent by being retried into one.
NEGATIVE_TTL = 300.0


@dataclass
class Leg(Generic[T]):
    """One cached, independently-failing source.

    ``fetch`` returns ``(value, error)`` — an empty ``error`` means success.
    The leg owns the TTL, the retry gate and the health flags, so the caller
    never has to reconstruct them.
    """

    fetch: Callable[[], tuple[T, str]]
    ttl: float
    empty: T
    negative_ttl: float = NEGATIVE_TTL

    _value: T = field(init=False)
    _at: float = field(default=0.0, init=False)
    _error: str = field(default="", init=False)
    _retry_at: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        self._value = self.empty

    @property
    def ok(self) -> bool:
        """Whether the last attempt reached the source."""
        return not self._error

    @property
    def error(self) -> str:
        return self._error

    @property
    def stale(self) -> bool:
        """Serving a kept value because the newest attempt failed."""
        return bool(self._error) and bool(self._value)

    def value(self, now: float | None = None) -> T:
        """The cached value, refetching only when it is due.

        Returns the kept value even while it is stale — but ``ok``/``error``
        report the truth, so a caller that cares can tell.
        """
        now = time.time() if now is None else now

        # Fresh only when it is both in time AND error-free. The second half is
        # bug 2: without it, a value kept through a failure is served as clean.
        if self._value and not self._error and now - self._at < self.ttl:
            return self._value

        # Bug 1: the gate is an absolute deadline set when the failure happened,
        # not a comparison against the age of the stale value.
        if self._error and now < self._retry_at:
            return self._value

        value, error = self.fetch()
        if error:
            self._error = error
            self._retry_at = now + self.negative_ttl
            # Keep the previous value for the caller, but leave ``_error`` set
            # so it is never mistaken for fresh.
            return self._value

        self._value = value
        self._error = ""
        self._at = now
        return self._value

    def snapshot(self) -> dict[str, Any]:
        """The leg's health as plain data, for a status endpoint or a card."""
        return {"ok": self.ok, "error": self._error, "stale": self.stale}
