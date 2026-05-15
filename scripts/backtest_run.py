"""CLI: drive one full backtest run for a configured window.

Usage::

    PYTHONPATH=src python -m scripts.backtest_run --window svb-stress-2023-03

The script loads ``config/backtest_settings.json`` and
``config/backtest_windows.json`` via the ``Runner`` class, materialises a
run directory under ``<runs_root>/<run-id>/``, executes every scheduled tick,
and prints the run summary.  Exit code is non-zero if the run is aborted (i.e.
the failure-tick ratio was exceeded).
"""
from __future__ import annotations

import argparse
import logging
import sys

from backtest.runner import Runner


def main() -> None:
    """CLI entrypoint for a full backtest run.

    Parses ``--window`` (required) from ``sys.argv``, delegates to
    ``Runner().run()``, and exits with code 1 if the run was aborted.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Run one full backtest window.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  PYTHONPATH=src python -m scripts.backtest_run "
            "--window svb-stress-2023-03"
        ),
    )
    parser.add_argument(
        "--window",
        required=True,
        metavar="KEY",
        help="window key in config/backtest_windows.json",
    )
    args = parser.parse_args()

    result = Runner().run(args.window)

    print(f"run_id:  {result.run_id}")
    print(f"run_dir: {result.run_dir}")
    print(f"status:  {result.status}")

    if result.status == "aborted":
        sys.exit(1)


if __name__ == "__main__":
    main()
