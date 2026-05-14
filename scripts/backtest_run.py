"""CLI: drive one full backtest run for a configured window.

Reads the window key from ``config/backtest_windows.json``, populates the
cache providers, and runs the tick-loop driver.  Exits with code 0 on success
or code 1 if the run aborts due to excessive failures.

Usage::

    PYTHONPATH=src python -m scripts.backtest_run --window svb-stress-2023-03

Prerequisites:
    - ``backtests/cache/store.sqlite`` must be pre-populated by
      ``scripts/backtest_fetch.py`` for the chosen window.
    - ``config/backtest_settings.json`` and ``config/backtest_windows.json``
      must exist at the project root.
"""
from __future__ import annotations

import argparse
import logging
import sys


def main() -> None:
    """CLI entrypoint — parses arguments, runs the backtest, exits appropriately."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run one backtest window end-to-end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--window",
        required=True,
        help="Window key defined in config/backtest_windows.json.",
    )
    args = parser.parse_args()

    from backtest.runner import Runner

    result = Runner().run(args.window)

    print(f"run_id:  {result.run_id}")
    print(f"run_dir: {result.run_dir}")
    print(f"status:  {result.status}")

    if result.status in ("aborted", "interrupted"):
        sys.exit(1)


if __name__ == "__main__":
    main()
