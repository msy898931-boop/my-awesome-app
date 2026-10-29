# AYZR — a separate, AI-first gold engine

AYZR is a second engine that lives **beside** GoldBot, not inside it. GoldBot
decides with arithmetic and uses a model to *narrate*; AYZR asks a model to
**decide**, then checks its answer against the arithmetic before anything is
recorded. The point of running both is to find out, over a month of shadow
trading, whether a model weighing the whole picture picks better trades than the
rules do — and better than a coin.

Nothing here touches the main bot. AYZR has its own settings, its own AI key and
daily quota, its own journal on its own disk path, and its own Telegram panel.
Running AYZR cannot spend the bot's AI budget, cannot read or write the bot's
state or archive, and cannot break a card the bot already sends.

---

## What it reads, and how it decides

For each run AYZR reads, in one pass:

| Layer | Source | What it contributes |
|-------|--------|---------------------|
| Structure | `smc/engine.py` (shared, read-only) | BOS/CHoCH, premium/discount, liquidity, sweeps, FVGs, order blocks, ATR |
| Calendar | `news/window.py` | high-impact windows, blackout |
| Headlines | `news/rss.py` | WSJ Markets, Investing Commodities, Investing Forex |
| Macro | `news/macro.py` | EURUSD, real 10-year yield |

The model receives all of it and returns a **decision**, not a description:
market state, bias, the technical and economic/political read, two scenarios
(primary and alternative, each with its trigger and invalidation), a confidence,
and a trade.

## The arithmetic guard

Every trade the model proposes is validated against the fact sheet before it is
journalled. Four checks, each of which can reject:

1. **Side** — the stop is on the correct side of the entry, the target on the
   far side.
2. **Floor** — the implied reward-to-risk clears AYZR's own minimum.
3. **Provenance** — the entry sits inside a real unfilled FVG or unmitigated
   order block, or on a named structural level. A level the model cannot point
   to in the facts is treated as invented.
4. **Reachability** — the entry is not absurdly far from price, which is the
   shape an invented level usually takes.

A rejected trade is recorded **with its reason**, never silently repaired. When
the month is reviewed, "the model proposed 12 trades, 5 were rejected for a bad
stop side" is a finding, and it is lost the moment a rejection is swallowed.

## The journal, and the control that makes it honest

Every signal is written to an append-only JSONL file (`signals.jsonl`) **with a
matched control**: the same entry, risk and target distance, but a coin-flip
direction. This is the part that matters. Gold trends, so a directional engine
can look clever for a month by doing nothing more than being long during an
up-month. Scoring the signal against a coin flip removes that — if the two
columns end up the same, AYZR is adding no timing, whatever its total R says.

Outcomes are resolved later against the real bars that followed, using the
project's own tested fill rules (`backtest/engine.py::evaluate_trade`): a resting
entry only fills if price traded there, and the stop is checked before the target
within each bar. A signal that never fills is recorded as `no_fill`, not as a
win or a loss.

## The report

`report` answers *what happened to the trades you gave me?* It reports three
things and refuses to blur them: **today**, **all time**, and a **verdict line**
that states plainly whether the sample is yet large enough to mean anything.
Below 100 resolved signals it says "not yet" before it says anything else,
because a small sample that *looks* positive is the easiest way to fool
yourself.

## Running it

```bash
# one full pass: read, ask, validate, journal — prints the card
PYTHONPATH=src python3 -m goldbot.ayzr.cli analyse

# the journal's tally: today, all time, and the sample verdict
PYTHONPATH=src python3 -m goldbot.ayzr.cli report

# settle open signals against the bars that formed — no model call
PYTHONPATH=src python3 -m goldbot.ayzr.cli resolve
```

In Telegram, AYZR has its own row on the panel: **🤖 تحليل AYZR** runs a full
pass, **📒 دفتر AYZR** shows the journal report. Both are reached only through
the `ayzr:` namespace, so an AYZR button can never collide with a bot command.

## What it needs

Only a key. Everything else has a working default.

| Input | Required? | What it is |
|-------|-----------|------------|
| `AYZR_AI_KEY` | recommended | A **second** free Groq key, so AYZR's calls cannot touch the bot's daily quota. Falls back to `GROQ_API_KEY` if unset, so AYZR works out of the box. |
| `AYZR_DATA_DIR` | no | Where AYZR keeps its journal and quota counter. Defaults to `<GOLDBOT_DATA_DIR>/ayzr` or `.goldbot/ayzr`. |
| `AYZR_TF` | no | The frame AYZR signals on. Default `M15`. |
| `AYZR_CONTEXT_TFS` | no | Frames read for context. Default `M15,M30,H1,H4`. |
| `AYZR_MIN_RR` | no | AYZR's own reward-to-risk floor. Default `2.0`. |
| `AYZR_AI_MODEL` / `AYZR_AI_FALLBACKS` | no | The model chain. Default `openai/gpt-oss-120b` then `openai/gpt-oss-20b`. |
| `AYZR_AI_DAILY_CAP` | no | AYZR's own per-day ceiling. Default `24`. |

The market data itself is keyless (Kraken/Binance), as it is for the bot.

## The one-month shadow test

AYZR logs every signal and its outcome but places nothing. After about a month
the `report` verdict, and the gap between the signal column and the control
column, are what decide whether AI replaces the plan or sits beside it. That
choice stays with the trader; the engine's job is to make the evidence honest
enough to choose on.
