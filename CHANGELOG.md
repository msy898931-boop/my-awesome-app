# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
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
- `news/rss.py` refuses an oversized body and any document carrying a
  `DOCTYPE`/`ENTITY` declaration before parsing it, the stdlib-only defence
  against entity-expansion attacks. Verified against all three live feeds,
  which carry no DOCTYPE.
- The suite is now measured, not asserted. `coverage` runs the tests in CI with
  a **floor of 94%** (`[tool.coverage.report] fail_under`), so the figure can no
  longer drift away from the truth unnoticed. Measured on this revision: **95%**
  of `src/goldbot`, with no module below 85%; `format.py`, `prompt.py`,
  `health.py`, `pipeline.py` and the data base are at 100%.
- The blackout is now **enforced**, not merely printed. A high-impact window
  withholds the direction and setup from the automatic push; the idea is
  remembered and sent when the window closes, and survives a restart.
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
