"""AI analyst — an SMC/ICT persona reasoning over computed facts.

The model never invents a level. It is handed the fact sheet the engine
computed and asked only to reason about it: to narrate the market's state in
the trader's language, to say which setup is present, and to justify a plan in
terms of the facts it was given. If it returns a number that is not in the
sheet, that number is discarded — the deterministic layer owns the levels.

Without a key the engine still runs: this module returns ``configured: false``
and a deterministic Arabic summary built from the facts, so a caller degrades
instead of erroring.
"""

from .analyst import AnalysisResult, Analyst
from .prompt import SYSTEM_PROMPT, build_prompt

__all__ = ["Analyst", "AnalysisResult", "build_prompt", "SYSTEM_PROMPT"]
