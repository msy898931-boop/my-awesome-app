# Architecture

How a read flows from the network to a card, module by module. Read
[PORTFOLIO.md](../PORTFOLIO.md) for the *why*; this is the *what* and *where*.

---

## One read, end to end

```
                      ┌─────────────────────────────────────────┐
   Telegram  ────────▶│  telegram/bot.py   poll, dispatch, tick  │
   button/command     │  telegram/screens.py  one screen/command  │
                      │  telegram/menus.py    buttons, help text  │
                      └───────────────┬─────────────────────────┘
                                      │  Pipeline.run(with_ai, with_news)
                                      ▼
                      ┌─────────────────────────────────────────┐
                      │  pipeline.py    orchestrates, never      │
                      │                 decides                  │
                      └───┬───────────┬───────────┬─────────────┘
                          │           │           │
              ┌───────────▼──┐  ┌─────▼──────┐  ┌─▼──────────────┐
              │ data/feed.py │  │ smc/       │  │ news/          │
              │ providers →  │  │ engine.py  │  │ calendar, rss, │
              │ archive →    │  │ primitives │  │ macro, window  │
              │ Series       │  │ FactSheet  │  │ NewsPicture    │
              └──────────────┘  └─────┬──────┘  └────────────────┘
                                      │
                                ┌─────▼──────────┐
                                │ strategy/      │
                                │ plan.py        │  entry / stop / target, R:R
                                │ risk.py        │  position sizing
                                └─────┬──────────┘
                                      │
                                ┌─────▼──────────┐   (only when asked)
                                │ ai/analyst.py  │   Groq narrative
                                └─────┬──────────┘
                                      │
                                ┌─────▼──────────┐
                                │ telegram/      │
                                │ menus.py       │  buttons, help
                                │ screens.py     │  command -> screen
                                │ bot.py         │  loop, state
                                │ format.py      │  Arabic cards
                                └────────────────┘
```

`pipeline.py` is 136 lines. It calls each stage, records a failure per stage
into `result.errors`, and returns. It contains no market judgement — that is
the whole point of it being separate.

---

## Layers

### `data/` — providers, and a local archive

| module | role |
|---|---|
| `base.py` | `Series`, `Bar`, the `Provider` protocol, `FetchReport` |
| `kraken.py` | PAXG/USD live candles — spot-linked, leads the choice |
| `binance.py` | PAXGUSDT deep history via `data-api.binance.vision` |
| `goldapi.py` | spot cross-check only (serves no candles) |
| `yahoo.py` | `GC=F` futures, disabled by default (contango premium) |
| `feed.py` | tries providers in order, folds results into the archive, reads from it when offline |
| `archive.py` | de-duplicated, sorted, atomic local store of candles |

`feed.py` is where "the network is unreliable" is absorbed: a provider failing
is a `FetchReport` entry, not an exception, and the archive means a restart is
not blind. Free sources hand back about a week of intraday, so the archive is
the only way to build a sample worth backtesting on.

### `smc/` — the fact engine

`primitives.py` holds ATR, swing detection, structure; `engine.py` turns a
`Series` into a `FactSheet` per timeframe: structure (BOS/CHoCH),
premium/discount position, liquidity pools, sweeps, fair-value gaps, order
blocks, session ranges. It computes; it does not decide what to trade.

### `strategy/` — plan and risk

`plan.py` builds a plan from a setup's own zone (entry, stop, target) and
enforces the minimum reward-to-risk ratio. `risk.py` sizes a position. A plan
that cannot meet the R:R floor is **rejected with a reason**, not emitted with
a poor ratio.

### `backtest/` — the honest scoreboard, the metrics, and the control

`engine.py` walks the strategy forward over closed bars — no look-ahead, a limit
entry that only fills if price actually reaches it, the stop assumed hit before
the target when one bar touches both — and charges a cost per trade, so the
reported numbers are net. `report.py` judges the result: expectancy, profit
factor, payoff, drawdown in R, losing streaks, Sharpe and Sortino per trade,
each a pure function of the trade list. A metric that cannot be computed is
`None` and prints as an em dash, never as a zero. `study.py` runs the whole
thing as a deep study — the longest series per frame, contiguous out-of-sample
windows, and the control — reached by `cli study`. `control.py` answers the
question the scoreboard cannot: given the *same trade geometry* but **random
entry times**, how often does chance do as well? It builds two surrogate sets —
one drawn from the whole walkable window, one matched to each trade's own trend
regime — and a cell is called `skill` only if it beats both at α = 0.05, `beta`
if it loses to either, `inconclusive` if the sample cannot decide. The
distinction between the two is the whole exercise: on the historical data a
long-gold bias beat a naive random control, and only the regime-matched control
exposed it as drift. The study scores each setup against its *own* control, not
just the pooled average — a single real edge or a single drifter would otherwise
vanish inside the headline verdict — and the report prints that per-setup
verdict beside its expectancy. `chart.py` draws the cumulative-R curve with no
plotting dependency — a text chart for a terminal and a standalone SVG for a
browser, sharing one mapping onto a grid, with the zero line drawn only when the
curve crosses it. `research/study_control.py` runs it over the archive.

### `news/` — calendar, wires, macro, blackout

| module | role |
|---|---|
| `calendar.py` | Forex Factory mirror (`ff_calendar_thisweek.json`), keyless |
| `rss.py` | three keyless RSS feeds, labelled honestly |
| `macro.py` | FRED CSV + EURUSDT — dollar and real yield as **context** |
| `window.py` | assembles the four into one `NewsPicture`, with flags |

The blackout gate is the important one, and it **fails closed** — but on the
right fact. Three facts about the window are kept distinct, because they call
for different answers:

- a high-impact window is **open** — the idea is withheld and the card says so;
- no window was found on a read we **hold** — the idea goes out;
- the calendar **could not be read at all** — the idea is withheld anyway,
  because an unknown window is not a clear one, and the card says the read
  failed.

"A read we hold" is the key refinement. The source rate-limits by IP, so a 429
is routine, and the last good read is **saved to disk** and restored on the next
start. A blocked fetch therefore degrades to a *stale but usable* calendar — the
window is known, just not this minute's — rather than to "unknown", which would
withhold every idea. The fallback is bounded: a saved read is refused once it is
older than `MAX_STALE` or once every event in it is already in the past, so it
can only ever describe the week we are actually in. The `usable` flag carries
this distinction into the digest: `checked=False` (and therefore withholding) is
now reserved for the case where there is genuinely nothing to reason about.

The rule holds on **every** path — the price card still goes out with the reason
flagged, and the idea follows once a read succeeds.

### `ai/` — the narrative layer, and nothing else

`analyst.py` calls Groq with a compact fact sheet; `prompt.py` builds the
payload. The model adds prose to a read that already exists. Without a key the
pipeline produces the full deterministic plan, just without commentary — the AI
is an enrichment, never a dependency.

### `telegram/` — the surface

| module | role |
| --- | --- |
| `client.py` | the Bot API client — one guarded call per method |
| `menus.py` | buttons, help text, and the payload resolver; pure data and functions |
| `screens.py` | the dispatcher: one screen per command, as a mixin the bot inherits |
| `alerts.py` | when to speak up about news, unprompted; pure decision functions |
| `bot.py` | the loop and the state — poll cadence, pushed card, health port |
| `format.py` | the Arabic cards |

`client.py` is a thin Bot API client. `menus.py` holds the buttons and the
help text — pure data and pure functions, so a keyboard can be tested on its
own. `screens.py` is the dispatcher: one screen per command, as a mixin the bot
inherits. `alerts.py` decides, as a pure function, which releases deserve an
unprompted heads-up; it is clock-free and network-free, so the decision can be
tested on its own. `bot.py` owns the loop and the state — the poll cadence, the
pushed card, the persisted preferences, and the health port. `format.py`
renders the Arabic cards. The bot answers **only** the chat in
`TELEGRAM_CHAT_ID` — every other chat is ignored and logged, because an answer
is a market read and a read can cost an AI call.

---

## The invariants

These hold across the codebase and are what the tests defend:

1. **Advisory only.** No order path, no broker credentials, anywhere.
2. **A failure is reported, never rendered as a calm default.** `ok` / `error`
   / `stale` travel in the data, not rebuilt per formatter.
3. **A number carries its provenance.** Where it survived a failure it carries
   the observation day (`as_of`).
4. **Degrade, do not crash.** Every network leg falls back and records why.
5. **Fail closed, not open.** When a check cannot run, the guarded action is
   withheld — an unread calendar withholds an idea just as an open window does.
   "We could not check" is never treated as "nothing to find". The one nuance is
   a *saved* read: a blocked fetch falls back to the last good calendar (bounded
   by age and by holding an event still ahead), which is a known window, not an
   unknown one — so it does not withhold.
6. **The AI is optional.** Every read exists without it, and the bot's own use
   of it is capped per day so a metered tier cannot be spent into a silent,
   broken-looking state.
7. **Speak before, not during.** The heads-up for a high-impact release goes out
   inside a lead window, because once the blackout opens the idea is already
   withheld — a warning delivered then is a warning too late.
8. **Pure standard library.** No runtime dependency, ever (see CONTRIBUTING).

---

## Configuration

Every tunable lives in `config.py`; the rest of the package never reads
`os.environ` directly. Defaults work with **no keys at all** — the providers are
keyless and the AI degrades to a deterministic read. `load_dotenv` reads a
`.env` if present, but real environment variables always win, so a
shell-exported secret is never clobbered by a stale file.

---

## Running it

```bash
python -m goldbot.cli analyse     # full SMC/ICT read as JSON
python -m goldbot.cli card        # the Telegram card as text
python -m goldbot.cli news        # calendar, headlines, macro
python -m goldbot.cli bot         # the polling loop
python -m goldbot.cli backtest    # the historical study
python -m goldbot.cli study       # the deep study (metrics, windows, control)
```

Or as a container — see the Docker section of the [README](../README.md).
