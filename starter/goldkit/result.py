"""The result envelope: data that always carries its own health.

The bug this exists to prevent: a fetch helper that swallowed every error into
``[]``. A Cloudflare ``429`` then looked identical to a genuinely quiet week,
and the UI printed the reassuring default ("the window is calm") while a
scheduled rate decision sat unread in the feed.

So a read is not ``data``; it is ``data + ok + error``, and the two travel
together so a formatter cannot accidentally print one without the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class Result(Generic[T]):
    """A value plus the honest state of the attempt that produced it.

    ``ok`` means "the source answered". It does **not** mean "the answer was
    non-empty": an empty list from a healthy feed is ``ok=True`` with no data,
    and that is a different fact from a dead feed, which is ``ok=False``.
    """

    value: T
    ok: bool = False
    error: str = ""
    stale: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        # Truthiness tracks health, not content, so ``if result:`` reads as
        # "did the read succeed" — which is what a caller almost always means.
        return self.ok

    @classmethod
    def good(cls, value: T, **meta: Any) -> Result[T]:
        """A read that reached its source. ``value`` may still be empty."""
        return cls(value=value, ok=True, meta=meta)

    @classmethod
    def failed(cls, error: str, value: T) -> Result[T]:
        """A read that did not reach its source.

        ``value`` is whatever the caller wants to fall back to — usually the
        previous good read (so it is marked ``stale``) or an empty collection.
        The error is never dropped, and a stale fallback is never returned as if
        it were fresh.
        """
        return cls(value=value, ok=False, error=error, stale=bool(value))

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": self.ok}
        if self.error:
            out["error"] = self.error
        if self.stale:
            out["stale"] = True
        if self.meta:
            out.update(self.meta)
        out["value"] = self.value
        return out
