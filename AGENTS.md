# AGENTS.md

Instructions for any agent (human or AI) working in this repository. Read this
first; it is the shortest path to a change that fits.

## What this project is

A Telegram bot that reads the gold market (XAUUSD) through SMC/ICT and explains
what it sees in Arabic. **Advisory only** — it never places an order and holds
no broker credentials. Do not add an execution path.

## The one rule that outranks the rest

**Honesty over features.** If the numbers do not support a trade, the code must
say so rather than emit a plausible-looking one. A failed read must be reported
as a failure, never rendered as a reassuring default. Tests assert these
refusals; keep them. See `PORTFOLIO.md` for why this is the design's spine.

## Commands

```bash
python -m pip install -e ".[dev]"   # install with dev tools
make check                          # the whole gate: lint, format, types, security, audit, tests
make fix                            # autofix lint and formatting
make test                           # the suite only
make cov                            # the suite under coverage, floor enforced
make docker                         # build the container image
```

Run the bot locally:

```bash
PYTHONPATH=src python -m goldbot.cli analyse   # JSON read
PYTHONPATH=src python -m goldbot.cli card      # the Telegram card as text
PYTHONPATH=src python -m goldbot.cli bot       # the polling loop
```

## Layout

```
src/goldbot/
  models.py     value objects (Bar, Quote, Series)
  net.py        the single guarded HTTP door
  config.py     every tunable, read from the environment once
  data/         providers (kraken, binance, goldapi, yahoo) + archive + feed
  smc/          the deterministic fact engine (structure, liquidity, FVG, OB)
  strategy/     plan builder and position sizing
  news/         calendar, RSS wires, macro context, blackout window
  ai/           the narrative layer, and nothing else
  telegram/     the surface: menus (buttons/text), screens (dispatcher), bot (loop)
  health.py     the /health endpoint and the self-ping
starter/goldkit/  the extracted, reusable patterns (own tests)
research/         one-off studies (not in the runtime)
tests/            the suite
```

`docs/ARCHITECTURE.md` has the data flow end to end.

## Conventions

- **Python 3.10+**, standard library only at runtime. Dev tools are the only
  extras. Do not add a runtime dependency without a very good reason.
- **Code and comments in English; user-facing strings in Arabic.**
- Type hints everywhere — `mypy` is in the gate.
- `ruff` owns formatting (line length 100). Run `make fix`, do not hand-format.
- Comments explain *why*, not *what*. The existing files are the reference; if a
  comment would just restate the line below it, delete it.

## Tests

- The suite is **offline by design**: `tests/conftest.py` blocks every
  non-loopback socket. A red run always means the logic broke, not that a feed
  was down. If a test needs the network, it needs a stub instead.
- Prefer real code paths. `monkeypatch` a function; avoid `Mock` unless there is
  no alternative, and say why when you do.
- The `starter/` kit is in the same gate — its tests run, its types are checked,
  and it counts toward the coverage floor. Keep it that way.

## Commits and PRs

One idea per commit, imperative subject, and a body that says what was wrong and
why the fix is right. Reference the failure you are preventing, not just the
change you made. A change is ready when `make check` is green.
