"""Backtest — an honest study of the setups on historical bars.

The point of this module is to answer one question truthfully: if the strategy
had been run on the past, how often would its plans have worked? Everything in
it exists to keep that answer honest.

  * **No look-ahead.** A plan is built from bars up to and including bar *i*,
    then evaluated on bars *i+1* onward. The facts a plan cites are the facts
    that existed when it would have been made.
  * **No cherry-picking.** Every signal the rules produce is evaluated. The
    result counts losses, not just wins.
  * **Costs are charged.** Spread and commission are subtracted from every
    trade, because a backtest without costs is a fantasy.
  * **The outcome is decided, not guessed.** A trade is a win only if the
    target is touched before the stop, on the bars that followed.
"""

from .engine import Trade, BacktestReport, run_backtest, evaluate_trade

__all__ = ["Trade", "BacktestReport", "run_backtest", "evaluate_trade"]
