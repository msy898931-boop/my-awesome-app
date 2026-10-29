# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **A professional backtest with a matched control.** `cli study` measures the
  strategy over the deepest series each frame can offer: headline metrics,
  contiguous time windows, and a control that re-runs the trades' own geometry
  at random entry times (plus a regime-matched draw). The verdict is split into
  `skill` / `beta` / `inconclusive` instead of asserted as one number, and a
  metric that cannot be computed prints as an em dash, never a zero. On 833 days
  of H1 the answer is **beta** — a long-gold bet wearing an SMC costume. The
  study also scores each setup against its *own* control, so a single cell can
  be judged on its own: on H1, BOS continuation read `skill` (+0.355R, random
  p=0.003, regime p=0.012) where sweep reversal was negative — but the same
  cell on H4 (an independent, 2253-day sample) read `beta` (+0.021R, p=0.72).
  A single-frame `skill` verdict is therefore **not** evidence; it has to
  replicate across frames to count, and this one does not.
- **An equity curve you can actually look at.** `cli study --chart` prints
  the cumulative-R curve as a text chart, and `--svg FILE` writes it as a
  standalone SVG — both from `backtest/chart.py`, with no plotting
  dependency. The zero line is drawn only when the curve crosses it, so its
  absence is itself information; an empty curve is named, never drawn.
### Fixed
- **`نسبة النجاح` meant two different things in two layers.** The metric
  block (`report.metrics`, behind `cli study`) counted a win as *positive R*,
  which swept in timeouts that merely closed up, while the walk engine's own
  summary (`cli backtest`) counted a win as *the target being hit*. The same
  name gave two numbers for the same run. Both now use the target/stop
  binary, so a timeout is a win in neither; profit factor still partitions on
  the sign of R, because it is money over every filled trade. A new
  `الحصيلة` line prints the win/loss/timeout split, so the smaller win-rate
  denominator is explained rather than mysterious, and the sample now carries
  its **calendar dates** (`2024-07-18 → 2026-10-29`), because "833 days"
  says nothing about which 833 the result was measured in.
### Added
- **A curve per setup, not just the pooled one.** `cli study --chart-setups`
  prints one text curve for each setup and `--svg-dir DIR` writes the pooled
  curve plus one SVG per setup (`all.svg`, `استمرار-BOS….svg`, …), with the
  file names derived from the labels so a rerun overwrites rather than
  duplicates. The pooled curve averages three different bets; seeing them
  apart is what shows a setup that climbs while another bleeds — the shapes
  match the verdicts (BOS climbs, CHoCH oscillates, the sweep spikes then
  bleeds). The per-setup finals sum exactly to the pooled final, which the
  suite asserts.

### Fixed
- **A rate-limited calendar silently withheld every trade idea.** The economic
  calendar source (`nfs.faireconomy.media`) rate-limits by IP, and the bot runs
  on a shared host, so a 429 was routine rather than exceptional. The blackout
  gate fails closed on an unread calendar — correct, since an unknown window is
  not a clear one — but with no fallback a *transient* block became "could not
  check", which suppressed every idea until the next successful fetch. On a
  restart the read was lost entirely, so the bot spent most of its life unable
  to show a setup. The last good read is now saved to disk
  (`GOLDBOT_DATA_DIR/calendar.json`) and restored on start, so a blocked fetch
  degrades to a *stale but usable* calendar: the window is known, and the idea
  goes out, flagged as based on saved data. The fallback is bounded — refused
  once older than `MAX_STALE` or once every event in it is in the past — so it
  can only ever describe the current week. `checked=False` and the resulting
  withholding are now reserved for the case where there is genuinely nothing to
  reason about. Found by running `python -m goldbot.cli card` live and reading
  the reason it printed.
- **The AI layer was effectively disabled in production.** Two faults stacked:
  the reply budget defaulted to 3000 tokens, which a reasoning model spends on
  its scratchpad before emitting anything, so `openai/gpt-oss-20b` returned a
  400 `json_validate_failed` (the JSON was cut mid-object); and that 400 was
  re-raised, which ended the model chain instead of trying the next model. With
  the primary out of daily quota, both paths failed and every card fell back to
  the deterministic read. The budget is now 6000 (measured: valid JSON twice in
  a row), and a malformed reply advances the chain. The failure was found by
  running `python -m goldbot.cli card`, not by reading the code.

### Added
- **A professional backtest: deep data, out-of-sample windows, and every
  headline metric.** The old `backtest` command walked whatever the archive
  held — two weeks — and printed a total. `backtest/report.py` now computes
  what a trader actually asks of a result: expectancy, profit factor, payoff,
  max drawdown in R, longest losing streak, Sharpe and Sortino per trade, and a
  best/worst trade. Each is a pure function of the trade list, so it is
  deterministic and testable without a feed. The rule that governs all of it:
  a metric that cannot be computed is `None` and prints as an em dash, **never
  as zero** — a thin sample must not read as a real number. `backtest/study.py`
  runs it as a study: it loads the deepest series each frame can offer (the
  paging Binance feed reaches years, so the sample is long enough to judge), cuts
  the trades into contiguous out-of-sample windows and scores each one so a
  result carried by a single stretch of months is exposed as a regime, and puts
  the whole sample through the matched control so the number is split into skill
  and drift rather than asserted. New command: `study --tf H1 --windows 5`, with
  a printable report and a `--json` form. On 833 days of H1 (800 filled trades)
  it returns **beta**, the same verdict the earlier work reached by hand — now
  reproduced end to end on a sample twenty times longer.
- **A matched control for the strategy, so its return can be split into skill
  and drift.** The historical study concluded the setup is "beta to gold, not
  skill" from the regime table alone; that conclusion is now measured rather
  than argued. `backtest/control.py` re-runs the strategy's own trades beside
  surrogate sets that share *every* frozen field — direction, risk distance,
  reward-to-risk, cost, fill window, horizon — and differ only in the **entry
  time**, drawn at random. Two controls defeat two confounds: `random` asks
  whether the geometry alone was lucky, and `regime` draws only from bars in the
  trade's *own* trend regime, so the drift it inherited is held constant and
  only the timing within that regime is free. A cell is called `skill` only when
  it beats both at α = 0.05; `beta` when it loses to either; `inconclusive`
  when the sample cannot decide — and `inconclusive` is never printed as a pass.
  The window the surrogates draw from is the span the real trades occupy, not
  the whole sample: a control allowed to draw from a different stretch of a
  trending sample would let a difference in drift read as timing skill. That
  distinction is not cosmetic — it moved the control's measured false-positive
  rate on zero-skill entries from **63% to 0%** across a 40-seed panel, and a
  test now guards it.
- **`research/study_control.py`** — runs the control over the archive, cell by
  cell (frame, direction, setup), and prints each verdict with both gaps and
  both p-values. On the current two-week archive most cells are `inconclusive`
  (too few fills to judge, reported as such) and the one cell large enough to
  call — M30 sell — is `beta`, consistent with the earlier finding.
- **`GOLDBOT_DATA_DIR`, a single root for everything that must outlive the
  process** — the bot's state file and the candle archive. On a host with an
  ephemeral filesystem (Render's free plan among them) a restart used to reset
  the per-day AI counter, so the cap that exists to protect a metered free tier
  reset on every deploy, and the announced-news set reset with it, so a restart
  re-fired the day's alerts. `render.yaml` now mounts a 1 GB disk at
  `/var/data` and points the root at it; the Dockerfile uses the same variable
  so the state is durable there too. Empty keeps the historical relative paths,
  so nothing changes locally. An explicit `GOLDBOT_ARCHIVE_DIR` still wins over
  the root, as a reader would expect.

- **The bot now works unprompted.** Two things it used to make you ask for now
  arrive on their own. A **news heads-up** fires inside a lead window
  (`GOLDBOT_NEWS_ALERT_MINUTES`, default 30) before a high-impact release, with
  the event, its UTC clock time, and its forecast and previous — sent *before*
  the blackout opens, because a warning delivered during the window is a warning
  too late. And the **automatic push now carries the whole trade**: entry, stop,
  target and R:R, plus the model's read, where before it printed a direction and
  a setup and nothing a trader could act on. An event is announced exactly once
  (keyed by country, title and scheduled time, persisted), so a restart or a
  repeated read cannot turn one release into a stream of alerts.
- **A hard daily cap on the bot's own AI calls** (`GOLDBOT_AI_DAILY_CAP`,
  default 30). The free tier meters 200k tokens per model per day and a full
  fact sheet is ~3.4k, so a loop that asks on every new idea can spend the day's
  budget in an hour and then read as broken until midnight. When the cap is
  spent the card loses the commentary, never the trade. The counter is per UTC
  day and persisted, so a restart does not reset it.
- **Repository governance files.** `SECURITY.md` (private disclosure route and
  the narrow threat model), `CODE_OF_CONDUCT.md`, `AGENTS.md` (the instructions
  an agent needs on arrival), `.editorconfig`, `.github/CODEOWNERS`, and issue
  and pull-request templates. A repo a stranger can contribute to needs to say
  how, and what it will not accept.
- **A secret scan in CI and in `pre-commit`.** `gitleaks` runs on every change
  with full history, so a committed key is caught at the door rather than
  discovered after it has already been pushed. The pre-commit hooks also now
  cover `starter/`, which the linters already checked but the hooks did not.
- **Tests for the loop itself** (`tests/test_bot_loop.py`): the first-tick
  priming, the send-failure and rejected-send log lines, the malformed-update
  guards, the `run_forever` cadence (including the skip until the interval is
  due), and the `PORT` / self-ping wiring. `telegram/bot.py` went from 79% to
  99% covered; the suite from 539 to 563 tests.
- **A container image (`Dockerfile`, `docker-compose.yml`).** Two stages: a
  wheel is built, then installed into a runtime that carries only the
  interpreter and the package — no build toolchain, no pip cache. The process
  runs as an unprivileged user (`uid 10001`) with a read-only root filesystem,
  no privilege escalation, a memory ceiling, and the archive on a named volume
  so a restart no longer loses the accumulated candles. A `HEALTHCHECK` reads
  the same `/health` endpoint a host would. No secret is baked into any layer;
  the token, chat id and Groq key are injected at run time.
- **A Docker job in CI.** The image is built on every change, started with a
  throwaway token, and the job fails unless the container answers its own
  health endpoint and does not run as root — so a broken build is caught before
  a deploy rather than at one.

- **`PORTFOLIO.md`**, an engineering narrative: the decisions and the
  measurements behind them, written for someone reading the repository to judge
  the work rather than to run the bot.
- **`docs/ARCHITECTURE.md`**, a data-flow and module map with the invariants the
  tests defend.
- **`starter/` — `goldkit`, the reusable patterns extracted and tested.** Four
  pieces, each the fix to a bug that shipped: `Result` (data that carries its own
  `ok`/`error`/`stale`, truthy on health not content), `Leg` (a cached source
  with an honest freshness test and a retry gate set to an absolute deadline),
  `ResilientSource` (several legs, `ok` only when all answered), and
  `http.guarded_url` / `read_capped` (the scheme guard and a body-size cap). 20
  tests pin the refusals, and the kit is checked, typed and covered by the same
  gate as the project it came from.
- A `make docker` / `make docker-run` target, and `GOLDBOT_ENABLE_MACRO` added
  to `.env.example` (it was missing).
- **A macro context layer (`news/macro.py`), stated as context and never as a
  signal.** The bot now reads the dollar and the real yield from two keyless
  sources — FRED's CSV (`DFII10`, `DTWEXBGS`, `T10YIE`) and `EURUSDT` from the
  same Binance host already used for candles — and puts one honest word on the
  card: tailwind, headwind, mixed, or a stated "unknown". When a plan leans into
  the wind it says so out loud ("الشراء يسبح عكس اتجاه الدولار — هذا بيتا على
  الذهب، لا حافة").
  Two measurements, run before any of it was written, are why it is context and
  not a filter: the gold/dollar link is **contemporaneous, not leading**
  (EURUSD's prior-bar move correlates with gold's move at +0.004 daily, -0.045
  intraday), and gating gold momentum on dollar agreement made the result
  **worse** (5-day lookback: mean +0.020% per bar alone versus -0.017% with the
  filter). The layer is off-switchable with `GOLDBOT_ENABLE_MACRO=0`.
  The honesty rules match the calendar's: a failed read is reported, never
  rendered as a calm macro; a kept-but-stale number is not served as clean; and
  a quiet macro says `unknown`, which is a different claim from `neutral`.
- Macro context is carried into the AI prompt as a labelled note ("سياق فقط …
  لا يغيّر اتجاه الخطة"), and omitted entirely when it could not be read.
- 39 tests for the macro layer, its voting, its failure reporting and its card
  rendering. Total coverage 95→**96%** (519 tests).
- **Static security analysis and a dependency audit in CI.** `bandit` scans the
  package and `pip-audit` checks the project's own dependencies against the
  advisory database; both run on Python 3.10 and 3.12 and fail the build on a
  finding. `make check` runs them too, so the gate is the same locally and on
  the runner. The audit is scoped to the project (`.`), not the whole
  environment — auditing everything flags the runner's `setuptools` and
  pip-audit's own transitive deps, which this project does not ship.
- **Dependabot** (`.github/dependabot.yml`): weekly, grouped updates for the
  Python dev tooling and the pinned actions. The repo is public, so this is free
  — and a vulnerable pin now arrives as a PR rather than as a red audit with no
  upgrade path ready.
- **`net.open_url`, one guarded door for outbound HTTP.** Every `urlopen` call
  site now goes through it, and it refuses any scheme that is not `http`/`https`
  *before* urllib is reached. `urlopen` will open `file://` and other schemes,
  and several URLs are assembled from configuration or a provider's response, so
  the check belongs in one place rather than repeated (and forgettable) at each
  call site.
- Tests for `net.py` (each refused scheme, each allowed one, and a prepared
  `Request`), and for the blackout policy on the plan card.
- **A second coverage push, driven by the report rather than by taste.** Every
  module below 85% was taken on: `ai/prompt.py` 50→100%, `ai/analyst.py` 75→98%,
  `telegram/bot.py` 79→85%, `telegram/format.py` 84→100%, `data/yahoo.py`
  23→98%, `health.py` 83→100%, `backtest/engine.py` 54→97%, `pipeline.py`
  71→100%. Total coverage 79→**95%**, the floor raised 78→94%.
- Tests for the HTTP providers (`data/yahoo.py`, `data/goldapi.py`, the
  `http_get_json` helper, Binance's spot call), the backtest scoreboard
  (`_summarise`, `_walk`, `run_backtest`), the prompt builder and its token
  trim, the analyst's success path, the Arabic card renderers, the bot's command
  dispatcher, and the self-ping thread.
- **A chat allowlist on the bot.** `handle_updates` served *any* chat that
  messaged the bot, and an answer is a market read that can cost a Groq call, so
  anyone who found the bot could spend the day's quota. Only
  `TELEGRAM_CHAT_ID` is served now; a stranger is logged and dropped, and a
  missing chat id refuses everyone rather than admitting everyone.
- 21 tests for the previously untested surfaces: `cli.py` (every command, both
  failure paths of `send`, the `bot` guards) and the allowlist itself.
- `make cov` and a `coverage` dev dependency; CI now runs the suite under
  coverage and fails below the floor.
- CI workflow (`.github/workflows/ci.yml`): ruff, ruff-format, mypy and pytest
  on Python 3.10 and 3.12 — the declared floor and a current interpreter.
- `Makefile` with `check` / `fix` / `test` / `cov` targets, and a
  `.pre-commit-config.yaml` that runs the same linters before a commit.
- `LICENSE` (MIT) and this changelog.
- 14 tests covering the BOS continuation, sweep reversal and `_best_zone`
  retracement rule, which had no coverage.
- Blackout tests: the window withholds a new idea, the price card still goes
  out, and the withheld idea is delivered once the window closes.
- The RSS feeds now report their health like the calendar: `rss.ok`,
  `rss.error` and `rss.stale` are surfaced in the news digest and the card, so
  an empty headline list is no longer indistinguishable from a dead feed. A
  total outage keeps the last good read and backs off; one dead outlet does not
  sink the others.
- Tests for `strategy/plan.py` (the CHoCH retest path, the stop floor, the
  target gate), `news/rss.py`, `data/kraken.py` (the live candle source, which
  had none) and `config.py` (the `.env` parser and the coercion helpers).

### Changed
- **`telegram/bot.py` is split by responsibility.** `menus.py` holds the
  buttons, help text and payload resolver (pure data and functions);
  `screens.py` holds the command dispatcher as a mixin; `bot.py` keeps the loop
  and the state. The three files each have one job, and the names the old module
  exported are re-exported from `bot.py`, so no caller changed. The coverage
  floor rises to 96% and applies to `goldkit` as well as `goldbot` (measured
  98% across both, no module under 90%; 563 tests including the starter kit's).
- `news/rss.py` refuses an oversized body and any document carrying a
  `DOCTYPE`/`ENTITY` declaration before parsing it, the stdlib-only defence
  against entity-expansion attacks. Verified against all three live feeds,
  which carry no DOCTYPE.
- The suite is now measured, not asserted. `coverage` runs the tests in CI with
  a floor (`[tool.coverage.report] fail_under`), so the figure can no
  longer drift away from the truth unnoticed; `format.py`, `prompt.py`,
  `health.py`, `pipeline.py` and the data base are at 100%.
- The blackout is now **enforced on every path**, not merely printed. A
  high-impact window withholds the direction and setup from the automatic push;
  the idea is remembered and sent when the window closes, and survives a restart.
  The card a trader asks for (`/plan`, and the CLI) is held to the same rule: the
  blackout withholds the entry, stop, target and the AI's confidence, and the
  no-trade card says *which* of the two it is — no setup, or a setup withheld —
  so a suppressed idea is never mistaken for a flat market. Previously the rule
  was honoured on the push and bypassed by the button, which is not a rule.
- The test suite is genuinely offline: `conftest` blocks every non-loopback
  socket and zeroes the AI retry back-off. The run went from ~163s to under 2s.
- Bot state is written atomically (temp file + `os.replace`), so a crash
  mid-write cannot leave a half-state the next start reads as truth.
- The bot loop logs through `logging` instead of `print`.
- The README's plan-identity line now says *direction plus setup*, matching the
  code; the test count is corrected; the environment table is complete.
- `.env.example` drops the dead `GOLDBOT_ALERT_MODE` and documents
  `GROQ_FALLBACK_MODELS`, `GROQ_MAX_TOKENS`, `GOLDBOT_CACHE_SECONDS`,
  `GOLDBOT_ENABLE_ARCHIVE` and `GOLDBOT_ARCHIVE_DIR`.

### Fixed
- **The blackout failed open on a calendar it could not read.** `digest` sets
  `active=False` when the fetch fails — an unread window is not an *open* one —
  but `blackout_blocks_idea` consulted only `active`, so a rate-limited feed
  silently *authorised* the idea and the card printed every number as if the
  rule had been checked and passed. A `429` on the calendar was, in effect, a
  licence to trade into a possible FOMC release. The predicate now fails
  **closed**: an unread calendar withholds the idea exactly as an open window
  does, and the cards name which of the three facts they hold — a window is
  open, no window was found, or the window could not be read — so "no news" is
  never confused with "could not tell". `format_price` carries the reason
  explicitly, because it cannot tell a withheld idea from an absent one on its
  own, and the withheld idea is still delivered once a read succeeds.
- `telegram/bot.py` `_save` created the state directory *outside* its `try`, so
  an unwritable state path raised `OSError` straight out of `handle_updates` —
  which the run loop only guards against `TelegramError`. A read-only mount or a
  directory removed under the bot would have stopped it for good. The
  `makedirs` now sits inside the guarded block; a failed save is skipped, not
  fatal, and the next tick retries.
- `models.Bar.is_sane` now rejects a **zero or negative** price. The
  `clean_bars` docstring already promised to drop the zero-price seed row, but
  the check let it through, so an empty provider row could reach ATR and every
  structure level as a real price.
- `data/binance.py` filtered `None` rows *after* reading `page[0].time`, a
  crash risk on a partial page; the filter now runs first.
- `research/study_robust.py` closed over a loop variable (ruff B023), so it
  captured the last value instead of its own.
- `ai/analyst.py` called `float()` on values that could be `None`; the check
  now precedes the conversion.
- The repo is clean under ruff, ruff-format and mypy (was 11 mypy errors).

## [0.1.0] — the working analyst

The state the project reached before the review: a live SMC/ICT reader with a
deterministic plan, a news-aware card, an AI narrative, and a historical study
that found the setup is beta to gold rather than skill.
