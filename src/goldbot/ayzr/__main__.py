"""Run AYZR as its own service: ``python -m goldbot.ayzr``.

A module ``__main__`` so the service has one obvious entrypoint, which is what
a host's start command should name. It runs :class:`~.bot.AyzrBot`, which owns
AYZR's Telegram surface, its shadow runner, and its own health port.
"""

from __future__ import annotations

import logging
import sys

from .bot import AyzrBot


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    AyzrBot().run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
