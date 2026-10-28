# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
to follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- CI workflow (`.github/workflows/ci.yml`): ruff, ruff-format, mypy and pytest
  on Python 3.10 and 3.12 — the declared floor and a current interpreter.
- `Makefile` with `check` / `fix` / `test` targets, and a `.pre-commit-config.yaml`
  that runs the same linters before a commit.
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
- Coverage rose from 84% to 88%; the critical modules are now `plan.py` 95%,
  `rss.py` 97%, `kraken.py` 97%, `config.py` 96%.
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
