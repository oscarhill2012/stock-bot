"""CLI: regenerate the report for an existing backtest run directory.

Useful when you want to re-run reporting after patching ``reporting.py``
without re-running the full backtest.  Loads backtest settings via
``get_backtest_settings()`` and resolves the run directory as
``<runs_root>/<run-id>/``.

Usage::

    PYTHONPATH=src python -m scripts.backtest_report --run-id svb-stress-2023-03-abc1234
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from backtest.reporting import report
from backtest.settings import get_backtest_settings


def main() -> None:
    """Parse CLI arguments and delegate to ``backtest.reporting.report``.

    Loads backtest settings via ``get_backtest_settings()``, resolves the run
    directory, and calls ``report()`` to generate ``report/equity_curve.png``
    and ``report/metrics.md``.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Regenerate the report for an existing backtest run.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Run identifier (e.g. svb-stress-2023-03-abc1234).",
    )
    args = parser.parse_args()

    settings = get_backtest_settings()
    run_dir  = Path(settings.runs_root) / args.run_id

    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    report(run_dir, settings)
    print(f"report written under {run_dir / 'report'}")


if __name__ == "__main__":
    main()
