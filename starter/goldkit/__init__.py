"""goldkit — the honesty-first plumbing extracted from GoldBot.

Every feed-backed tool eventually hits the same four problems, and each has a
wrong answer that looks right until it costs you:

1. **A failed fetch rendered as a quiet result.** An empty list from a dead
   source looks exactly like a genuinely quiet day. ``Result`` keeps ``ok`` and
   ``error`` beside the data so the two can never be confused.
2. **A retry storm against a source that just said no.** Retrying a ``429`` on
   the next call keeps it refusing. ``Leg`` holds an explicit retry gate set to
   an absolute deadline, which is the only form that actually fires.
3. **A cached-but-stale value served as fresh.** ``Leg`` refuses to treat a
   value as clean while an error is on record for it.
4. **Reading an unbounded response.** ``read_capped`` stops a hostile or broken
   body before it is in memory, and ``guarded_url`` refuses any scheme that is
   not web traffic.

These are not abstractions for their own sake. Each one is the fix to a bug
that shipped in GoldBot and was seen on a real card; the tests in
``starter/tests`` assert the refusals so they cannot quietly regress.

Pure standard library, like the project it came from.
"""

from .cache import NEGATIVE_TTL, Leg
from .http import ALLOWED_SCHEMES, UnsafeURL, guarded_url, open_url, read_capped
from .result import Result
from .source import ResilientSource

__all__ = [
    "ALLOWED_SCHEMES",
    "NEGATIVE_TTL",
    "Leg",
    "ResilientSource",
    "Result",
    "UnsafeURL",
    "guarded_url",
    "open_url",
    "read_capped",
]
