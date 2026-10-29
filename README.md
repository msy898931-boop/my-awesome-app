# GoldBot — XAUUSD SMC/ICT Telegram Analyst

A Telegram bot that reads the gold market (XAUUSD) through the lens of
**SMC/ICT** — market structure, liquidity, order blocks, fair value gaps,
sessions — and explains what it sees in plain Arabic. It also reads the
economic calendar and the news wires and puts them on the card, so a plan is
never read without knowing what is about to print.

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
  windows) and gold-relevant RSS headlines. A high-impact blackout withholds a
  new idea on **every** path — the price card still goes out with the window
  flagged and the idea follows once the window closes, and the card a trader
  asks for shows the read without the entry/stop/target, saying plainly that a
  setup is withheld rather than pretending the market is flat.
- **Names the macro wind, and does not pretend to trade it.** Gold's level
  correlation with the broad dollar is about **-0.6**, and this bot reads the
  dollar (EURUSD, live) and the real yield (FRED) so a card can say which way
  the wind blows — and say so plainly when a plan is leaning into it. It is
  **context, never a signal**: the relationship is contemporaneous, not
  leading, and gating the setups on it made the backtest *worse*. See
  `news/macro.py` for the two measurements behind that choice.
- **Asks an AI for the narrative.** With a Groq key the bot adds a written
  read on top of the deterministic facts; without a key it still produces the
  full SMC/ICT plan, just without commentary.
- **Sends only what is new.** A card goes out when the plan's identity
  (direction plus setup) changes. A plan that wobbles by a tick is the same
  idea, and repeating it is noise. A restart never replays the day's alerts.

---

## Commands

```bash
python -m goldbot.cli analyse     # full SMC/ICT read as JSON
python -m goldbot.cli card        # the Telegram card as text
python -m goldbot.cli news        # calendar and headlines
python -m goldbot.cli send        # send one card to the configured chat
python -m goldbot.cli bot         # run the polling loop
python -m goldbot.cli backtest    # the historical study
python -m goldbot.cli study --chart --svg curve.svg   # deep study + equity curve
python -m goldbot.cli study --chart-setups --svg-dir curves/   # one curve per setup
python -m goldbot.cli study --chart-time   # the pooled curve against dates, not rank
```

Flags: `--no-ai` skips the AI layer, `--no-news` skips the news layer,
`--json` prints raw JSON.

### In Telegram

The bot opens with a persistent panel; every action is also available as a
typed command.

- `/start` or `/help` — the panel and the command list
- `/تحليل` — the full SMC/ICT read, frame by frame
- `/صفقة` — the current plan or market state
- `/أخبار` — calendar and headlines
- `/حالة` — a quick summary
- `/تحديث` — read again now, bypassing the cache
- `/إعدادات` — timeframes and sensitivity

The bot works **unprompted**. Two things arrive on their own:

- a **news heads-up** inside a lead window (`GOLDBOT_NEWS_ALERT_MINUTES`,
  default 30) before a high-impact release — the event, its UTC clock time, and
  its forecast and previous. It is sent *before* the blackout opens, because a
  warning delivered during the window is a warning too late. Each event is
  announced exactly once, even across restarts.
- a **trade card** when the idea is new, carrying the price, the direction, the
  setup, and the numbers — entry, stop, target, R:R — plus the model's read.

The AI is asked only when a card is actually due, and within a hard daily cap
(`GOLDBOT_AI_DAILY_CAP`, default 30): a metered free tier is easy to spend into
a silent, broken-looking state. When the cap is reached the card loses the
commentary, never the trade. The deterministic plan is always there.

The bot answers **only the chat named by `TELEGRAM_CHAT_ID`**. Every other chat
is ignored and logged: an answer is a market read, and a read can cost an AI
call, so the door is shut rather than merely discouraged. With no
`TELEGRAM_CHAT_ID` set, the bot answers nobody.

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

### Docker

The image is built in two stages and carries no secrets — the token, the chat
id and the Groq key are injected at run time.

```bash
cp .env.example .env      # then fill in your values
docker compose up -d --build
docker compose logs -f
```

It runs as an unprivileged user with a read-only root filesystem, and the
archive lives on a named volume so a restart does not lose the accumulated
candles. A `HEALTHCHECK` reads the same `/health` endpoint the host does. For a
single container without Compose:

```bash
docker build -t goldbot:local .
docker run -d --name goldbot \
  -e TELEGRAM_BOT_TOKEN -e TELEGRAM_CHAT_ID -e GROQ_API_KEY \
  -e PORT=8080 -p 127.0.0.1:8080:8080 -v goldbot-archive:/data \
  goldbot:local bot
```

### Environment

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather (required for the bot surface) |
| `TELEGRAM_CHAT_ID` | Where cards are sent, and the **only** chat the bot will answer |
| `GROQ_API_KEY` | Groq key. Optional — without it the bot still produces plans |
| `GROQ_MODEL` | Default `openai/gpt-oss-120b` |
| `GROQ_FALLBACK_MODELS` | Tried in order when the primary's quota is spent |
| `GROQ_MAX_TOKENS` | Reply cap (default 6000; a reasoning model spends the budget on its scratchpad before emitting JSON, and a cap of 3000 truncated the reply into a `json_validate_failed` 400) |
| `GOLDBOT_SYMBOL` | Default `XAUUSD` |
| `GOLDBOT_TIMEFRAMES` | Default `M5,M15,M30,H1,H4` |
| `GOLDBOT_BARS` | Bars per frame (default 220) |
| `GOLDBOT_MIN_RR` | Minimum reward-to-risk (default 2.0) |
| `GOLDBOT_POLL_SECONDS` | Loop interval (default 60) |
| `GOLDBOT_CACHE_SECONDS` | Reuse a read within this window (default 90) |
| `GOLDBOT_NEWS_ALERT_MINUTES` | Heads-up this many minutes before a high-impact release (default 30; 0 disables) |
| `GOLDBOT_AI_DAILY_CAP` | Ceiling on the bot's own AI calls per UTC day (default 30) |
| `GOLDBOT_ENABLE_*` | Toggle each data provider (`KRAKEN`, `BINANCE`, `GOLDAPI`, `YAHOO`) |
| `GOLDBOT_ENABLE_ARCHIVE` / `GOLDBOT_ARCHIVE_DIR` | Local archive on/off and where it lives |
| `GOLDBOT_DATA_DIR` | Root for the state file, the archive, and the saved calendar read. Point it at a mounted volume so a restart keeps the AI cap, the announced news, and the calendar |
| `GOLDBOT_ENABLE_MACRO` | Macro context (dollar, real yield) on/off. Context only, never a signal |
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
| FRED CSV (`DFII10`, `DTWEXBGS`, `T10YIE`) | **Macro context** — real yield, broad dollar, inflation expectation. Keyless CSV, daily |
| `binance:EURUSDT` | **Live dollar proxy** for the macro context. Keyless, same host as the candles |

The feed folds every fetch into a local archive (de-duplicated, sorted,
atomic write) and reads from it when the network is unavailable. With Binance
feeding it, the archive is deep immediately, which makes the research scripts
**exactly reproducible**.

---

## Tests

The suite is offline: `tests/conftest.py` blocks every non-loopback socket, so
a run is deterministic and a failure means the logic broke, not that a feed was
down.

```bash
make check                     # ruff + format + mypy + bandit + pip-audit + pytest
make cov                       # the suite under coverage, floor enforced
python -m pytest tests/ -q     # 687 tests for the bot
```

CI runs the suite under `coverage` and **fails below a floor of 96%** of
`src/goldbot` + `starter/goldkit` (measured 97% on the current revision,
no module under 90%), so the number cannot drift
away from the truth unnoticed.

The extracted [starter kit](starter/README.md) carries its own tests, run by
the same gate and counted in the same coverage floor.

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
- A 1 GB disk mounted at `/var/data`, with `GOLDBOT_DATA_DIR` pointed at it, so
  the state file and the archive survive the restarts Render makes freely. The
  state carries the per-day AI counter and the set of news already announced:
  lose it and the cap resets on every deploy, and a restart re-fires the day's
  alerts.

A `Dockerfile` and `docker-compose.yml` are also included (see
[Docker](#docker)) for any host that runs containers, or for running it
locally. The image is built and started in CI on every change, so a broken
build is caught before a deploy rather than at one.

### Keeping a free web service awake

Render spins a Free web service down after 15 minutes without inbound traffic,
which stops the market poll. Two facts drive the setup:

- The workspace gets **750 free instance hours per month**. Running 24/7 for a
  31-day month is 744 hours — almost no headroom, and exhausting them suspends
  *every* free service in the workspace until the next month.
- Gold trades 24/5, so the weekend needs no poll.

**The service guards itself.** Render exposes the service's own public URL as
`RENDER_EXTERNAL_URL`, and `goldbot.health` pings it every 10 minutes. That
request is inbound traffic, so the idle timer never expires. This is the
reliable path: a GitHub Actions `schedule` is best-effort and can be delayed
well past the 15-minute window, so it is only a backup for the case where the
process is wedged and its own ping thread is dead with it.

`RENDER_HEALTH_URL` remains useful for that backup workflow, and for any
external monitor (cron-job.org, UptimeRobot) pointed at `/health`:

1. Render dashboard → your service → copy its URL, e.g.
   `https://goldbot.onrender.com`
2. GitHub → repository → **Settings → Secrets and variables → Actions →
   Variables** → new variable `RENDER_HEALTH_URL` =
   `https://goldbot.onrender.com/health`
3. Optionally test it: **Actions → keepalive → Run workflow**.

The keepalive workflow runs **Monday to Friday only**, every 5 minutes: about
516 hours a month, comfortably inside the 750-hour budget.

The caveat that remains is Render's own: it "might restart a Free web service at
any time". A restart loses local files (the archive, `run/bot_state.json`), and
the bot re-primes without replaying alerts — so a restart is silent, not noisy.

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
  news/             economic calendar, RSS headlines, blackout, macro context
  ai/               Groq client and prompt
  telegram/         client, menus, screens, the polling bot
  backtest/         walk engine, metrics, study, control, equity curve
research/           reproducible robustness, sweep and control studies
starter/            goldkit — the reusable patterns, extracted and tested
docs/               architecture, data flow, module map
tests/              687 tests (+20 in starter/)
Dockerfile          two-stage image, non-root, healthcheck
docker-compose.yml  local run with the archive on a volume
```

Two companion documents go deeper than this README:

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — how a read flows from the
  network to a card, module by module, and the invariants the tests defend.
- **[PORTFOLIO.md](PORTFOLIO.md)** — the engineering decisions and the
  measurements behind them, written for someone judging the work.

---

## Scope

Separate from any other project: its own Groq key, its own Telegram bot, its
own configuration. No shared credentials.

The historical study (five years of data) found the setup is **beta to gold,
not skill** — a buy wins when the market rises and loses when it falls. The
one surviving cell (BOS continuation on H1) is positive in both regimes, but
its confidence interval includes zero and it decays in the second half, so it
was rejected. The bot is an analyst, not an edge.

That conclusion is now measured, not asserted. `backtest/control.py` re-runs the
strategy's own trades beside matched surrogates that share its geometry and
costs and differ only in the entry time, with a second control that draws only
from the trade's own trend regime. A cell has to beat both to count as skill;
on the archive, the one cell large enough to call is beta. See
`research/study_control.py`.

---

## Licence

MIT.
