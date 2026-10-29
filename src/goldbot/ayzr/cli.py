"""AYZR's command line — run the engine, read its journal, settle its trades.

Kept separate from the main bot's CLI so the two never share a parser or a
default. Every subcommand is usable on its own:

  * ``analyse`` — one full pass: read, ask, validate, journal. Prints the card.
  * ``report``  — the journal's tally: today, all time, and the sample verdict.
  * ``resolve`` — settle open signals against the bars that have formed, no
    model call, so the outcome side costs nothing.
  * ``journal`` — the raw records, for inspection.

Usage::

    PYTHONPATH=src python3 -m goldbot.ayzr.cli analyse
    PYTHONPATH=src python3 -m goldbot.ayzr.cli report
"""

from __future__ import annotations

import argparse
import json
import sys

from .cards import format_decision, format_report
from .config import AyzrSettings
from .engine import Engine
from .journal import read_all


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="goldbot.ayzr", description="AYZR — AI-first gold engine (separate from the bot)"
    )
    p.add_argument(
        "command",
        nargs="?",
        default="analyse",
        choices=["analyse", "analyze", "report", "resolve", "journal"],
        help="what to do",
    )
    p.add_argument("--no-ai", action="store_true", help="skip the model, resolve only")
    p.add_argument("--json", action="store_true", help="print the raw record as JSON")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = AyzrSettings()
    settings.ensure_dirs()
    engine = Engine(settings)

    if args.command in {"analyse", "analyze"}:
        run = engine.run(with_ai=not args.no_ai)
        if args.json:
            print(
                json.dumps(
                    {
                        "read": {
                            "ok": run.read.ok,
                            "errors": run.read.errors,
                            "series": run.read.series,
                        },
                        "decision": run.decision.as_dict(),
                        "resolved": run.resolved,
                        "error": run.error,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(format_decision(run.read, run.decision))
        return 0

    if args.command == "report":
        report = engine.report()
        if args.json:
            print(
                json.dumps(
                    {
                        "today": vars(report.today),
                        "total": vars(report.total),
                        "open": report.open_now,
                        "no_trade_total": report.no_trade_total,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(format_report(report))
        return 0

    if args.command == "resolve":
        n = engine.resolve_only()
        print(f"حُسمت {n} إشارة.")
        return 0

    # journal
    rows = read_all(settings)
    print(
        json.dumps(rows, ensure_ascii=False, indent=2)
        if args.json
        else f"{len(rows)} سطر في الدفتر."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
