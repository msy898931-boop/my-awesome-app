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

### `news/` — calendar, wires, macro, blackout

| module | role |
|---|---|
| `calendar.py` | Forex Factory mirror (`ff_calendar_thisweek.json`), keyless |
| `rss.py` | three keyless RSS feeds, labelled honestly |
| `macro.py` | FRED CSV + EURUSDT — dollar and real yield as **context** |
| `window.py` | assembles the four into one `NewsPicture`, with flags |

The blackout gate is the important one, and it **fails closed**. Three facts
about the window are kept distinct, because they call for different answers:

- a high-impact window is **open** — the idea is withheld and the card says so;
- no window was found on a **read that succeeded** — the idea goes out;
- the calendar **could not be read** — the idea is withheld anyway, because an
  unknown window is not a clear one, and the card says the read failed.

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
| `bot.py` | the loop and the state — poll cadence, pushed card, health port |
| `format.py` | the Arabic cards |

`client.py` is a thin Bot API client. `menus.py` holds the buttons and the
help text — pure data and pure functions, so a keyboard can be tested on its
own. `screens.py` is the dispatcher: one screen per command, as a mixin the bot
inherits. `bot.py` owns the loop and the state — the poll cadence, the pushed
card, the persisted preferences, and the health port. `format.py` renders the
Arabic cards. The bot answers **only** the chat in `TELEGRAM_CHAT_ID` — every
other chat is ignored and logged, because an answer is a market read and a read
can cost an AI call.

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
   "We could not check" is never treated as "nothing to find".
6. **The AI is optional.** Every read exists without it.
7. **Pure standard library.** No runtime dependency, ever (see CONTRIBUTING).

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
```

Or as a container — see the Docker section of the [README](../README.md).
