# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- The coverage floor now applies to `goldkit` as well as `goldbot`; the
  total stays 96% (539 tests including the starter kit's).
- `news/rss.py` refuses an oversized body and any document carrying a
  `DOCTYPE`/`ENTITY` declaration before parsing it, the stdlib-only defence
  against entity-expansion attacks. Verified against all three live feeds,
  which carry no DOCTYPE.
- The suite is now measured, not asserted. `coverage` runs the tests in CI with
  a **floor of 94%** (`[tool.coverage.report] fail_under`), so the figure can no
  longer drift away from the truth unnoticed. Measured on this revision: **95%**
  of `src/goldbot`, with no module below 85%; `format.py`, `prompt.py`,
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
