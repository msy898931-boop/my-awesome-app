"""Strategy — turns facts into a concrete, checkable plan.

This is the deterministic half of the decision. It reads the fact sheet and
looks for the setups the methodology names, in priority order. Every plan it
emits carries the levels it used, the reason it fired, and the R:R — so the AI
layer narrates a decision that was already justified, rather than inventing one.

A plan is only emitted when the structure actually supports it. No plan is a
valid, common answer, and is reported as such rather than filled with a weak
idea.
"""

from .plan import Plan, build_plan, pick_setup
from .risk import position_size

__all__ = ["Plan", "build_plan", "pick_setup", "position_size"]
