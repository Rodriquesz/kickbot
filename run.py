#!/usr/bin/env python3
"""Entry point: run this from cron every 10-15 minutes.

It only ever fills in tips that are still empty shortly before kickoff
(see TIP_LEAD_TIME_MINUTES in .env) - it never overwrites a tip you
already placed yourself.
"""

import argparse
import errno
import fcntl
import logging
import sys
from pathlib import Path

from kickbot.config import Config
from kickbot.kicktipp import LoginError, TippingError
from kickbot.notify import notify
from kickbot.tipper import run

LOCK_FILE = Path(__file__).parent / ".kickbot.lock"
LOG_FILE = Path(__file__).parent / "logs" / "kickbot.log"


def setup_logging(verbose: bool) -> None:
    LOG_FILE.parent.mkdir(exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would be tipped without submitting"
    )
    parser.add_argument("--headed", action="store_true", help="Show the browser window")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    setup_logging(args.verbose)
    logger = logging.getLogger("kickbot")

    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno in (errno.EACCES, errno.EAGAIN):
            logger.warning("Another kickbot run is already in progress, exiting")
            return 0
        raise

    try:
        config = Config.load()
        if args.headed:
            config = Config(**{**config.__dict__, "headless": False})

        try:
            run(config, dry_run=args.dry_run)
        except (LoginError, TippingError) as exc:
            logger.error("Run failed: %s", exc)
            notify(config, "kickbot: Lauf fehlgeschlagen", str(exc))
            return 1
        except Exception as exc:
            logger.exception("Unexpected error during run")
            notify(config, "kickbot: Unerwarteter Fehler", str(exc))
            return 1
        return 0
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()


if __name__ == "__main__":
    raise SystemExit(main())
