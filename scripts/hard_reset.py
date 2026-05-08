"""Hard-reset the StockBot DB. Archives every table then truncates the live ones.

Usage:
    PYTHONPATH=src python -m scripts.hard_reset
    PYTHONPATH=src python -m scripts.hard_reset --yes      # skip confirmation
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from lifecycle.hard_reset import hard_reset


def _resolve_default_db_url() -> str:
    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise SystemExit("STOCKBOT_ENV=prod requires DATABASE_URL")
        return url
    return "sqlite:///data/stockbot.db"


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db-url", default=None)
    p.add_argument("--archive-dir", default="data/archives")
    p.add_argument("--scheduler-job", default=os.environ.get("SCHEDULER_JOB"),
                   help="Cloud Scheduler job name to pause (skipped for SQLite)")
    p.add_argument("--watchlist", default="config/watchlist.json")
    p.add_argument("--broker-mode", default="paper")
    p.add_argument("--starting-capital", type=float, default=10000.0,
                   help="Starting capital of the run being archived")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    args = p.parse_args(argv)

    db_url = args.db_url or _resolve_default_db_url()
    archive_dir = Path(args.archive_dir)

    print("This will pause the scheduler, archive all StockBot state, and wipe live tables.")
    print(f"Archive will be written under: {archive_dir}")

    if not args.yes:
        confirm = input("Type 'RESET' to confirm: ").strip()
        if confirm != "RESET":
            print("Aborted.")
            sys.exit(1)

    watchlist: list[str] = []
    wl = Path(args.watchlist)
    if wl.exists():
        watchlist = json.loads(wl.read_text()).get("tickers", [])

    result = hard_reset(
        db_url=db_url,
        archive_dir=archive_dir,
        scheduler_job=args.scheduler_job,
        meta_extra={
            "watchlist": watchlist,
            "broker_mode": args.broker_mode,
            "starting_capital_of_archived_run": args.starting_capital,
            "git_sha": _git_sha(),
        },
    )

    if args.scheduler_job:
        print(f"Paused Cloud Scheduler job {args.scheduler_job}")
    rows = sum(result.row_counts.values())
    tables = len(result.row_counts)
    print(f"Archived {tables} tables, {rows} rows -> {result.archive_path}")
    print("Live tables truncated")
    print(f"Wrote {result.archive_path.with_suffix('.meta.json').name}")
    print()
    print("Next: reset Trading 212 practice account in the UI, then run:")
    print(f"  PYTHONPATH=src python -m scripts.initialise --capital {args.starting_capital:.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
