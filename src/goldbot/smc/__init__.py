"""SMC/ICT engine — deterministic market structure for gold.

The engine forms **no opinion**. It computes only what can be read off closed
bars: swing pivots, the last break of structure and change of character, the
premium/discount split of the active leg, equal-highs/equal-lows liquidity
pools and recent sweeps, fair-value gaps, order blocks, session ranges, and a
multi-timeframe confluence tally. The AI layer interprets these facts; the
numbers themselves are arithmetic.

The distinction that matters most in this methodology, and that this engine is
built around: a **body close** beyond a level breaks structure, while a wick
beyond a level that closes back inside is a **sweep** — a stop-hunt. Conflating
the two is the single most common way an SMC read goes wrong, so they are
detected separately and never merged.
"""

from .engine import FactSheet, build, timeframe_facts
from .primitives import atr, structure_events, swings

__all__ = ["FactSheet", "build", "timeframe_facts", "atr", "swings", "structure_events"]
