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
        choices=["analyse", "analyze", "card", "bot", "send", "news", "backtest", "study"],
    )
    parser.add_argument("--no-ai", action="store_true", help="skip the AI layer")
    parser.add_argument("--no-news", action="store_true", help="skip the news layer")
    parser.add_argument("--json", action="store_true", help="raw JSON output")
    parser.add_argument("--tf", default="", help="primary frame for the study (H1, H4, …)")
    parser.add_argument("--windows", type=int, default=5, help="out-of-sample windows")
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

    if args.command == "backtest":
        from .backtest.engine import run_backtest

        report = run_backtest(settings)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    if args.command == "study":
        from .backtest.report import format_report
        from .backtest.study import run_study

        study = run_study(settings, primary_tf=args.tf or None, window_count=args.windows)
        if args.json:
            print(json.dumps(study, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_report(study))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
