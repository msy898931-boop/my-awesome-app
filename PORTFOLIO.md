# GoldBot — engineering notes

This is not a README. The README says what the bot does. This says **why it is
built the way it is**, with the measurements and the mistakes that decided the
design, because those are the parts that do not survive in the code alone.

It is written for someone reading the repository to judge the engineering
rather than to run the bot.

---

## The one-paragraph version

A gold-market analyst for Telegram, 7,397 lines of source and 8,998 lines of
tests, **zero runtime dependencies** (pure standard library), 687 tests, 98%
coverage, and a CI gate that fails on a lint, a type, a security or a coverage
regression. It reads the market through SMC/ICT primitives, builds a plan,
names the news and the macro backdrop, and **says out loud when its own numbers
do not support a trade**. It never places an order. The hardest part was not
the market logic; it was refusing to let a failure look like a quiet day.

---

## 1. The decision that shaped everything: honesty is a feature

Every other bot in this space optimises for confidence. This one optimises for
**not lying**, and almost every non-obvious line in the codebase comes from
that. Four rules, applied everywhere:

**A check that is also an intervention cannot observe.**
The Render health check looked healthy exactly as long as I curled it, because
my curl *was* the traffic resetting the 15-minute idle timer. The test for
"does a free host sleep?" is to **stop touching it and read `uptime_seconds`**.
This is now why the health document reports uptime and cycle count rather than
a bare `200 ok`: a status that cannot distinguish "working" from "being poked"
is not a status.

**Silence must never be rendered as an all-clear.**
`CalendarFeed` used to swallow every fetch error into `[]`, so a Cloudflare
`429` was indistinguishable from a quiet week. The card printed
"النافذة: هادئة" (the window is calm) while a scheduled FOMC release sat in the
feed. A source that fails now produces `ok=False` with the reason, and the card
prints "تعذّر الفحص" (could not check). The reassuring default was the bug.

**Report the reason, not the exception class.**
`_call` collapsed every failure to `type(exc).__name__`, so a spent AI quota
surfaced to the user as a bare `RuntimeError`. The 429 body is now parsed, and
the card says "نفدت الحصة اليومية، يعود بعد 20m" (daily quota spent, back in
20m). A class name is not a diagnosis.

**A number must carry its provenance.**
The no-AI fallback set `confidence = frame alignment`, so a card with no model
behind it still read "ثقة التقدير: 61%" — the same 3/5 as the line beside it,
dressed as a probability. Frame agreement is not a chance of success. It is now
`0` and omitted. Where a number survives a failure it carries the day it was
observed (`as_of`) rather than pretending to be current.

These are not style preferences. Each one was a bug that shipped, was seen in a
real card, and was fixed at the source of the claim rather than in the
formatter.

---

## 2. Zero dependencies, on purpose

`pip install -e .` pulls nothing. No `requests`, no `pandas`, no `ccxt`. The
HTTP layer is `urllib` behind one guarded door (`net.py`), which validates the
URL scheme once so no call site can forget and open a `file://` URL assembled
from config or from a provider's response.

What this buys:

- **A supply chain that cannot be attacked** because there is nothing to
  attack. `pip-audit` runs in CI and reports nothing to audit; the point of the
  gate is that a dependency added later cannot arrive with a known CVE.
- **A build that cannot rot.** No version drift, no "works on my machine", no
  breakage when a transitive dependency changes.
- **A trivial install and a small image.** The container is 179 MB, of which
  the application is about 1 MB and the rest is the Python interpreter.

What it costs: I hand-wrote a minimal `.env` parser, a CSV reader for FRED, a
kline parser for Binance, and a health HTTP server. All four are small, tested,
and readable in one sitting — which, for this project, is a better trade than a
dependency.

---

## 3. The market logic, and the study that rejected it

The bot reads structure (BOS/CHoCH), premium/discount, liquidity pools, sweeps,
fair-value gaps, order blocks and sessions, and weights the timeframes (H4
carries 8× the vote of M5, halving per step down) so a clean higher-timeframe
trend is not outvoted by choppy lower frames.

Then I measured it. `research/study_robust.py` runs four checks on five years
of data, because a positive total on its own proves nothing:

1. **Bootstrap confidence interval** on mean R (5,000 resamples). If it
   straddles zero, the strategy is indistinguishable from breakeven.
2. **Split-half by time.** An edge that lives in one half only is a regime.
3. **Per setup and per direction**, to see whether a headline is carried by one
   cell that will not repeat.
4. **Cost sensitivity.** The backtest is frictionless; gold spreads are real,
   so the break-even cost per trade is reported.

The result, which is in the README and not buried here:

| frame | buy/up | buy/down | sell/up | sell/down |
|-------|--------|----------|---------|-----------|
| M15   | +0.426 | -0.547   | -0.421  | +0.500    |
| M30   | +0.501 | -0.418   | -0.428  | +0.434    |
| H1    | +0.583 | -0.326   | -0.586  | +0.474    |

**It wins when gold rises and loses when gold falls, in every direction and
every frame.** The buy "edge" and the sell "problem" are one fact seen twice:
the setup is *beta to gold, not skill*. The one cell that is not beta — BOS
continuation on H1 — is positive in both regimes and both halves, but its
confidence interval includes zero and it decays hard in the second half, so it
was rejected too.

**The consequence for the code is the important part.** The obvious response to
a study like this is to tune parameters until a cell turns green. The code
carries an explicit instruction not to: *do not tune parameters to "fix" this —
the problem is not the settings.* A false edge is worse than no edge, because
it gets traded.

The bot is therefore documented as **an analyst, not an edge**, and that is a
feature: it is honest about what it is.

---

## 4. The macro layer: measured, then deliberately kept as context

Gold's level correlation with the broad dollar is about **-0.6**, so the bot
was already reading the dollar without naming it. `news/macro.py` names it —
keyless FRED CSV (real yield `DFII10`, broad dollar `DTWEXBGS`, inflation
expectation `T10YIE`) plus `EURUSDT` from the Binance host already used for
candles.

The tempting move was to **filter** setups on macro agreement. I measured
first:

- the gold/dollar link is **contemporaneous, not leading** — EURUSD's prior-bar
  move correlates with gold's move at **+0.004** daily and **-0.045** intraday,
  i.e. no usable predictive content;
- gating gold momentum on dollar agreement made the result **worse**
  (5-day lookback: **+0.020%** per bar alone versus **-0.017%** with the filter).

So the layer never changes a direction. It names the wind — tailwind, headwind,
mixed, or a stated `unknown` — and when a plan leans into it, the card says so:
*"الشراء يسبح عكس اتجاه الدولار — هذا بيتا على الذهب، لا حافة"* (the buy is
swimming against the dollar — this is beta on gold, not an edge).

Two design details worth noting because they are the honesty rules applied to a
new module:

- **`unknown` is not `neutral`.** A quiet macro ("nothing to say") is a
  different claim from "no net lean", and collapsing them would be the same
  class of bug as the calendar's calm window.
- **The votes can disagree, and then the answer is `mixed`.** An even split is
  reported as a split rather than resolved by picking a side.

---

## 5. Cost control: the arithmetic that made the AI affordable

Groq's free tier is **200k tokens/day per model**, and a full fact-sheet call
is about 3,400 tokens. Two bugs stacked to drain it:

1. `tick()` called the model every 60 seconds regardless of whether a card
   would be sent — `960 × 3400 = 3.26M` tokens, or **16.3× the budget**.
2. `plan_key` identified an idea by `direction:entry:stop:target`, which drift
   by a tick, so one idea looked new almost every cycle — **8 cards in 40
   minutes, all "buy"**, about 250/day or **4.3× budget**.

The fixes are both structural, not a smaller budget:

- the automatic push is `format_price()` and **costs zero tokens** — it is
  entirely deterministic;
- `plan_key` is now `direction:setup`, so an idea that wobbles by a tick is the
  same idea;
- the AI runs only when the trader asks (`/plan`), enriching an existing read.

A per-day `429` now **parks** the model, because retrying it every cycle as if a
20-second backoff would clear a daily quota is precisely what prevents
recovery. Per-minute and per-day 429s are distinguished from the response body.

---

## 6. Deployment: reading the platform's rules as design constraints

The bot is a silent polling loop, so a host that expects a web service sees
nothing listening and kills it. `health.py` serves `/` and `/health` on `$PORT`
from a daemon thread, and reports the loop's real state.

The interesting part is the free tier. Render spins a Free service down after
15 minutes without inbound traffic, and the workspace gets **750 instance hours
per month** — running 24/7 for a 31-day month is 744 hours, leaving almost no
headroom before Render suspends *every* free service in the account. Two facts
follow:

- **Gold trades 24/5**, so the weekend needs no poll. The keepalive workflow is
  `*/5 * * * 1-5` — about 516 hours a month, comfortably inside the budget.
- **A GitHub Actions `schedule` is best-effort** and can be delayed well past
  15 minutes (observed: zero scheduled runs across 13+ slots in two hours), so
  it is only a backup. The reliable path is the service pinging its own public
  URL every 10 minutes, because that request *is* inbound traffic.

The container image (`Dockerfile`, two stages) makes the deployment durable
instead of lucky: the archive lives on a named volume, so a restart no longer
loses the accumulated candles, and the process runs as an unprivileged user
with a read-only root filesystem and no privilege escalation.

---

## 7. The test suite

687 tests, 98% coverage, ~25 seconds, and **offline by design** — so a red run
always means the logic broke, never that a feed was down. The coverage gate is
`fail_under = 96` with a comment that states the rule: *raise it as coverage
rises; never lower it to make a red run green.* `source = ["goldbot"]` (not a
path glob) is what makes coverage fail a module the suite never imports,
because a file that is never loaded would otherwise be invisible to the report.

The tests that matter most are the refusals:

- a failed calendar read must not look calm;
- a release with no baseline must not get an invented arrow;
- a buy into dollar strength must be named as beta;
- an unchecked macro must never read as calm;
- a cached-but-stale number must not be served as clean;
- a failed fetch must back off, not hammer.

Each of those is a bug that shipped once. They are tests now.

---

## 8. What this project is not

It is not profitable, and the README says so. It has no live track record, no
users beyond its author, and no moat — the licence is MIT, so anyone may fork
it. The study found the strategy is beta to gold, not skill, and the code
refuses to paper over that.

What it demonstrates is narrower and, I would argue, rarer: **building a system
that tells the truth about itself**, measuring before claiming, and encoding
the refusals as tests so they cannot quietly regress. The market logic is
standard SMC/ICT. The engineering discipline around it is the point.
