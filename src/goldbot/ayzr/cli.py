"""AYZR's command line — run the engine, read its journal, settle its trades.

Kept separate from the main bot's CLI so the two never share a parser or a
default. Every subcommand is usable on its own:

  * ``analyse`` — one full pass: read, ask, validate, journal. Prints the card.
  * ``report``  — the journal's tally: today, all time, and the sample verdict.
  * ``resolve`` — settle open signals against the bars that have formed, no
    model call, so the outcome side costs nothing.
  * ``loop``    — the shadow runner: a pass per bar, until interrupted. This is
    what makes the one-month test a test.
  * ``bot``     — AYZR as its own service: its own Telegram surface, its own
    shadow runner, its own health port. The same thing ``python -m goldbot.ayzr``
    runs, offered here so one module owns every AYZR entrypoint.
  * ``journal`` — the raw records, for inspection.

Usage::

    PYTHONPATH=src python3 -m goldbot.ayzr.cli analyse
    PYTHONPATH=src python3 -m goldbot.ayzr.cli report
    PYTHONPATH=src python3 -m goldbot.ayzr.cli loop
    PYTHONPATH=src python3 -m goldbot.ayzr.cli bot
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from .cards import format_decision, format_report
from .config import AyzrSettings
from .engine import Engine
from .journal import read_all
from .runner import ShadowRunner, interval_for


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="goldbot.ayzr", description="AYZR — AI-first gold engine (separate from the bot)"
    )
    p.add_argument(
        "command",
        nargs="?",
        default="analyse",
        choices=["analyse", "analyze", "report", "resolve", "journal", "loop", "bot"],
        help="what to do",
    )
    p.add_argument("--no-ai", action="store_true", help="skip the model, resolve only")
    p.add_argument("--json", action="store_true", help="print the raw record as JSON")
    p.add_argument(
        "--interval",
        type=int,
        default=0,
        help="seconds between passes in `loop` (0 = one bar of AYZR_TF)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="with `loop`, run a single pass and exit — for a cron or a smoke test",
    )
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

    if args.command == "loop":
        if args.once:
            # One pass and out: the shape a cron entry wants, and the shape a
            # smoke test wants. No thread, no sleep.
            runner = ShadowRunner(settings)
            print(runner.run_once())
            return 0
        runner = ShadowRunner(settings)
        if args.interval:
            runner.interval = interval_for(settings.primary_tf, args.interval)
        print(f"AYZR يعمل — دورة كل {runner.interval} ثانية. أوقفه بـ Ctrl+C.")
        try:
            runner.run_forever()
        except KeyboardInterrupt:
            print("توقّف.")
        return 0

    if args.command == "bot":
        # AYZR as a service: its own Telegram, its own runner, its own health
        # port. Imported here so the lighter subcommands never pay for it.
        from .bot import AyzrBot

        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        AyzrBot(settings).run_forever()
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
