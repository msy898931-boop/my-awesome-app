"""Command line: run the analysis once, or run the bot.

python -m goldbot.cli analyse          # print the full read as JSON
python -m goldbot.cli card             # print the Telegram card as text
python -m goldbot.cli bot              # run the polling loop
python -m goldbot.cli send             # send one card to the configured chat
python -m goldbot.cli backtest         # run the historical study
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys

from .config import load_settings
from .pipeline import Pipeline
from .telegram.format import format_news, format_plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="goldbot", description="XAUUSD SMC/ICT analyst")
    parser.add_argument(
        "command",
        nargs="?",
        default="card",
        choices=["analyse", "analyze", "card", "bot", "dual", "send", "news", "backtest", "study"],
    )
    parser.add_argument("--no-ai", action="store_true", help="skip the AI layer")
    parser.add_argument("--no-news", action="store_true", help="skip the news layer")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    parser.add_argument("--tf", default="", help="primary frame for the study (H1, H4, …)")
    parser.add_argument("--windows", type=int, default=5, help="out-of-sample windows")
    parser.add_argument("--chart", action="store_true", help="print the equity curve as text")
    parser.add_argument(
        "--chart-setups", action="store_true", help="print one text curve per setup"
    )
    parser.add_argument(
        "--chart-time", action="store_true", help="print the pooled curve against dates"
    )
    parser.add_argument(
        "--chart-time-setups", action="store_true", help="print each setup curve against dates"
    )
    parser.add_argument("--svg", default="", help="write the equity curve to this SVG file")
    parser.add_argument("--svg-dir", default="", help="write pooled + per-setup SVGs to this dir")
    args = parser.parse_args(argv)

    settings = load_settings()

    if args.command in {"analyse", "analyze"}:
        result = Pipeline(settings).run(with_ai=not args.no_ai, with_news=not args.no_news)
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "card":
        result = Pipeline(settings).run(with_ai=not args.no_ai, with_news=not args.no_news)
        print(format_plan(result.plan, result.analysis, result.news))
        return 0

    if args.command == "news":
        result = Pipeline(settings).run(with_ai=False, with_news=True)
        print(
            format_news(result.news)
            if not args.json
            else json.dumps(result.news, ensure_ascii=False, indent=2)
        )
        return 0

    if args.command == "send":
        from .telegram.bot import menu_keyboard
        from .telegram.client import Telegram, TelegramError

        result = Pipeline(settings).run(with_ai=not args.no_ai, with_news=not args.no_news)
        tg = Telegram(settings.telegram_token)
        try:
            ok = tg.send(
                settings.telegram_chat,
                format_plan(result.plan, result.analysis, result.news),
                keyboard=menu_keyboard(),
            )
        except TelegramError as exc:
            print(f"فشل الإرسال ❌ — {exc}")
            return 1
        print("أُرسلت ✅" if ok else "فشل الإرسال ❌")
        return 0 if ok else 1

    if args.command == "bot":
        from .telegram.bot import Bot

        if not settings.enable_bot:
            print("البوت معطّل (GOLDBOT_ENABLE_BOT=0)")
            return 1
        if not settings.telegram_token:
            print("لا يوجد TELEGRAM_BOT_TOKEN")
            return 1
        # The loop reports through ``logging``; give it a readable one-line
        # format on stderr so a host's log view stays legible.
        logging.basicConfig(
            level=logging.INFO,
            format="[%(asctime)s] %(message)s",
            datefmt="%H:%M:%S",
        )
        Bot(settings).run_forever()
        return 0

    if args.command == "dual":
        # Both bots on one host and one port — the free-tier shape. Imported
        # here so the lighter subcommands never pay for it.
        from .dual import run as run_dual

        if not settings.telegram_token:
            print("لا يوجد TELEGRAM_BOT_TOKEN")
            return 1
        return run_dual(settings)

    if args.command == "backtest":
        from .backtest.engine import run_backtest

        report = run_backtest(settings, primary_tf=args.tf or None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "study":
        from .backtest.chart import render_curve, render_svg, render_time_curve
        from .backtest.report import format_report
        from .backtest.study import run_study

        study = run_study(settings, primary_tf=args.tf or None, window_count=args.windows)
        if args.json:
            print(json.dumps(study, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_report(study))
            if args.chart:
                print()
                print(render_curve(study["equity_curve"]))
            if args.chart_setups:
                for setup, curve in study["setup_curves"].items():
                    print()
                    print(render_curve(curve, title=setup))
            if args.chart_time:
                print()
                print(render_time_curve(study["equity_points"]))
            if args.chart_time_setups:
                for setup, pts in study["setup_points"].items():
                    print()
                    print(render_time_curve(pts, title=setup))
        if args.svg:
            # utf-8 explicitly: the default locale on a bare host can be ascii,
            # which would refuse the SVG's own text labels.
            with open(args.svg, "w", encoding="utf-8") as handle:
                handle.write(render_svg(study["equity_curve"]))
            print(f"حُفظ المنحنى في {args.svg}")
        if args.svg_dir:
            written = _write_setup_svgs(study, args.svg_dir)
            print(f"حُفظت {written} منحنيات في {args.svg_dir}")
        return 0

    parser.print_help()
    return 1


def _slug(name: str) -> str:
    """A setup name as a safe, readable filename stem."""
    cleaned = re.sub(r"[^\w\u0600-\u06ff]+", "-", name, flags=re.UNICODE).strip("-")
    return cleaned[:60] or "setup"


def _write_setup_svgs(study: dict, out_dir: str) -> int:
    """Write the pooled curve and one SVG per setup into ``out_dir``.

    The x-axis is spaced by trade time (the points carry their clock), so a
    quiet quarter and a busy week are visibly different widths. File names come
    from the setup labels, so the same run twice produces the same files.
    Returns the number written.
    """
    from .backtest.chart import render_svg

    os.makedirs(out_dir, exist_ok=True)
    curves = [("all", study["equity_curve"], study.get("equity_points"))]
    curves += [
        (name, curve, study.get("setup_points", {}).get(name))
        for name, curve in study.get("setup_curves", {}).items()
    ]
    for name, curve, points in curves:
        path = os.path.join(out_dir, f"{_slug(name)}.svg")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(render_svg(curve, title=f"Equity curve (R) — {name}", points=points))
    return len(curves)


if __name__ == "__main__":
    sys.exit(main())
