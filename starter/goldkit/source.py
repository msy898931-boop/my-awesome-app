"""A source built from several independent legs.

The calendar and the macro layer in GoldBot are both this shape: a handful of
sources that fail independently, where a partial failure must be **visible** and
a total failure must not be reported as "checked".

The rule this encodes: ``ok`` is true only when *every* leg answered. One dead
leg makes the read partial, and a partial read is flagged rather than quietly
presented as whole.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .cache import Leg
from .result import Result


@dataclass
class ResilientSource:
    """A named group of legs, read together and reported honestly.

    Build one leg per independent source, then call :meth:`read` to get a
    :class:`Result` whose ``value`` is the merged mapping and whose ``ok`` /
    ``error`` describe the whole attempt.
    """

    name: str
    legs: dict[str, Leg] = field(default_factory=dict)

    def add(
        self,
        key: str,
        fetch: Callable[[], tuple[Any, str]],
        ttl: float,
        empty: Any = None,
    ) -> Leg:
        """Register a leg under ``key`` and return it (so its flags stay reachable)."""
        leg: Leg = Leg(fetch=fetch, ttl=ttl, empty=empty)
        self.legs[key] = leg
        return leg

    def read(self, now: float | None = None) -> Result[dict[str, Any]]:
        """Read every leg and merge the results into one honest envelope.

        A leg that failed keeps its previous value (the ``Leg`` handles that)
        and contributes its message to ``error``. ``ok`` is true only when no
        leg is in error.
        """
        values: dict[str, Any] = {}
        problems: list[str] = []
        any_value = False
        for key, leg in self.legs.items():
            value = leg.value(now)
            values[key] = value
            if value:
                any_value = True
            if not leg.ok:
                problems.append(f"{key}: {leg.error}")

        if problems:
            # ``any_value`` decides stale: a partial read that still carries
            # numbers is stale, a read with nothing at all is simply failed.
            return Result(
                value=values,
                ok=False,
                error="; ".join(problems),
                stale=any_value,
            )
        return Result.good(values, checked=True)

    def health(self) -> dict[str, Any]:
        """Per-leg health, for a status line or an endpoint."""
        return {
            "source": self.name,
            "ok": all(leg.ok for leg in self.legs.values()),
            "legs": {key: leg.snapshot() for key, leg in self.legs.items()},
        }
