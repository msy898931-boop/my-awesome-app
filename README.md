# GoldBot — XAUUSD SMC/ICT Telegram Analyst

A Telegram bot that reads the gold market (XAUUSD) through the lens of
**SMC/ICT** — market structure, liquidity, order blocks, fair value gaps,
sessions — and explains what it sees in plain Arabic. It also reads the
economic calendar and the news wires, and folds both into the read.

**Advisory only. It never places an order.** The bot has no broker
credentials and no order path; it polls the market, forms a view, and says
it. Everything it sends is a reading, not an instruction.

---

## What it does

- **Reads the market** across M5, M15, M30, H1 and H4 and builds a fact sheet
  per frame: swing structure (BOS/CHoCH), premium/discount position, liquidity
  pools, sweeps, fair value gaps, order blocks and session ranges.
- **Weights the frames.** Higher timeframes dominate: H4 carries eight times
  the vote of M5, halving per step down. A clean H4 trend is not outvoted by
  choppy lower frames.
- **Gates direction top-down.** A counter-bias setup is refused before it is
  built, so the bot does not keep emitting buys into a downtrend.
- **Builds a plan** with entry, stop and target from the setup's own zone, and
  requires a minimum reward-to-risk ratio (default 2.0).
- **Reads the news**: the economic calendar (high-impact events, blackout
  windows) and gold-relevant RSS headlines.
- **Asks an AI for the narrative.** With a Groq key the bot adds a written
  read on top of the deterministic facts; without a key it still produces the
  full SMC/ICT plan, just without commentary.
- **Sends only what is new.** A card goes out when the plan's identity
  (direction plus the three levels) changes. A plan that wobbles by a tick is
  the same idea, and repeating it is noise. A restart never replays the day's
  alerts.

---

## Commands

```bash
python -m goldbot.cli analyse     # full SMC/ICT read as JSON
python -m goldbot.cli card        # the Telegram card as text
python -m goldbot.cli news        # calendar and headlines
python -m goldbot.cli send        # send one card to the configured chat
python -m goldbot.cli bot         # run the polling loop
python -m goldbot.cli backtest    # run the historical study
```

Flags: `--no-ai` skips the AI layer, `--no-news` skips the news layer,
`--json` prints raw JSON.

### In Telegram

- `/start` or `/help` — the command list
- `/تحليل` — the full SMC/ICT read, frame by frame
- `/صفقة` — the current plan or market state
- `/أخبار` — calendar and headlines
- `/حالة` — a quick summary

---

## Install and run

Requires Python 3.10 or newer. **No third-party dependencies** — the package
is pure standard library, so install is trivial.

```bash
pip install -e .

cp .env.example .env      # then fill in your values
python -m goldbot.cli card
```

The settings module reads a `.env` file at startup if one exists. Real
environment variables always win, so a shell-exported secret is never
clobbered by a stale file. `.env` is gitignored; never commit it.

### Environment

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather (required for the bot surface) |
| `TELEGRAM_CHAT_ID` | Where cards are sent |
| `GROQ_API_KEY` | Groq key. Optional — without it the bot still produces plans |
| `GROQ_MODEL` | Default `openai/gpt-oss-120b` |
| `GOLDBOT_SYMBOL` | Default `XAUUSD` |
| `GOLDBOT_TIMEFRAMES` | Default `M5,M15,M30,H1,H4` |
| `GOLDBOT_BARS` | Bars per frame (default 220) |
| `GOLDBOT_MIN_RR` | Minimum reward-to-risk (default 2.0) |
| `GOLDBOT_POLL_SECONDS` | Loop interval (default 60) |
| `GOLDBOT_ENABLE_*` | Toggle each data provider |
| `GOLDBOT_ENABLE_BOT` | Master switch for the bot loop |
| `PORT` | When set, a health endpoint is served on it (for web hosts) |

---

## Data sources

All are keyless.

| Source | Role |
|---|---|
| `kraken:PAXGUSD` | **Live** candles. Tokenised allocated gold, spot-linked |
| `binance:PAXGUSDT` | **Deep history** via `data-api.binance.vision`, back to 2020-08-28 |
| `gold-api:XAU` | **Spot cross-check** only; serves no candles |
| Yahoo `GC=F` | **Disabled by default** — futures carry a contango premium and the endpoint rate-limits hard |

The feed folds every fetch into a local archive (de-duplicated, sorted,
atomic write) and reads from it when the network is unavailable. With Binance
feeding it, the archive is deep immediately, which makes the research scripts
**exactly reproducible**.

---

## Tests

```bash
python -m pytest tests/ -q     # 76 tests
```

---

## Deployment

The bot is a silent polling loop, so a host that expects a web service sees
nothing listening and kills it. When `PORT` is set, `goldbot.health` serves
`/` and `/health` from a daemon thread and reports uptime, cycle count and AI
state. Locally there is no `PORT`, so nothing is opened and the loop behaves
exactly as it does without the module.

`render.yaml` describes a free Render web service:

- Build: `pip install -e .`
- Start: `python -m goldbot.cli bot`
- Health check: `/health`
- Secrets (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GROQ_API_KEY`) are set
  in the dashboard and never committed

### Keeping a free web service awake

Render spins a Free web service down after 15 minutes without inbound traffic,
which stops the market poll. Two facts drive the setup:

- The workspace gets **750 free instance hours per month**. Running 24/7 for a
  31-day month is 744 hours — almost no headroom, and exhausting them suspends
  *every* free service in the workspace until the next month.
- Gold trades 24/5, so the weekend needs no poll.

So the pinger runs **Monday to Friday only** (`.github/workflows/keepalive.yml`,
every 10 minutes). That is about 516 hours a month, comfortably inside the
budget, and the bot simply re-primes when the service spins back up.

Set the endpoint once, then the workflow finds it:

1. Render dashboard → your service → copy its URL, e.g.
   `https://goldbot.onrender.com`
2. GitHub → repository → **Settings → Secrets and variables → Actions →
   Variables** → new variable `RENDER_HEALTH_URL` =
   `https://goldbot.onrender.com/health`
3. The scheduled workflow pings it; use **Actions → keepalive → Run workflow**
   to test it immediately.

Any external monitor (cron-job.org, UptimeRobot) works the same way if you
prefer not to use Actions — point it at `/health` every ten minutes.

**A paid background worker removes the caveat entirely:** change the service to
`type: worker` and a paid plan, drop `healthCheckPath`, and no pinger is needed.

---

## Layout

```
src/goldbot/
  cli.py            command line entry
  config.py         environment and .env loading
  pipeline.py       one read: data → facts → plan → AI → news
  health.py         health endpoint for web hosts
  data/             providers: kraken, binance, goldapi, yahoo, archive, feed
  smc/              primitives (ATR, swings, structure) and the fact engine
  strategy/         plan construction and position sizing
  news/             economic calendar, RSS headlines, blackout windows
  ai/               Groq client and prompt
  telegram/         client, formatting, the polling bot
  backtest/         the historical study engine
research/           reproducible robustness and sweep studies
tests/              76 tests
```

---

## Scope

Separate from any other project: its own Groq key, its own Telegram bot, its
own configuration. No shared credentials.

The historical study (five years of data) found the setup is **beta to gold,
not skill** — a buy wins when the market rises and loses when it falls. The
one surviving cell (BOS continuation on H1) is positive in both regimes, but
its confidence interval includes zero and it decays in the second half, so it
was rejected. The bot is an analyst, not an edge.

---

## Licence

MIT.
