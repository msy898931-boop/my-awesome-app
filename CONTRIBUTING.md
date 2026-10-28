# Contributing

Thanks for looking. This is a small, focused project; the bar for a change is
that it keeps the bot honest.

## Ground rules

- **Advisory only.** The bot never places an order and holds no broker
  credentials. Do not add an execution path.
- **No third-party runtime dependencies.** The package is pure standard
  library. Dev tools (pytest, ruff, mypy) are the only extras.
- **Honesty over features.** If the numbers do not support a trade, the code
  must say so rather than emit a plausible-looking one. Tests assert these
  refusals; keep them.

## Setup

```bash
python -m pip install -e ".[dev]"
pre-commit install        # optional, runs the linters on commit
```

## The gate

Everything CI runs, in one word:

```bash
make check     # ruff check + ruff format --check + mypy + pytest
make fix       # autofix lint and formatting
```

A change is ready when `make check` is green. The suite is offline by design —
`tests/conftest.py` blocks non-loopback sockets — so a red run always means the
logic broke, not that a feed was down. If a test needs the network, it needs a
stub instead.

The gate covers `starter/` as well as `src/` and `tests/`: the extracted
[starter kit](starter/README.md) is shipped code, so its tests run, its types
are checked, and it counts toward the same coverage floor.

## Style

- Code and comments in English; user-facing strings in Arabic.
- `ruff` owns formatting (line length 100) and lint. Do not fight it.
- Type hints everywhere; `mypy` is part of the gate.
- Comments explain *why*, not *what*. The existing files are the reference.

## Commits

One idea per commit, imperative subject, and a body that says what was wrong
and why the fix is right. Reference the failure you are preventing, not just
the change you made.
