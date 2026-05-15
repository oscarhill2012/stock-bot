"""CLI: regenerate the report for an existing backtest run directory.

Useful when you want to re-run reporting after patching ``reporting.py``
without re-running the full backtest.  Reads ``config/backtest_settings.json``
from the current working directory (the project root) and resolves the run
directory as ``<runs_root>/<run-id>/``.

Usage::

    PYTHONPATH=src python -m scripts.backtest_report --run-id svb-stress-2023-03-abc1234
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from backtest.reporting import report


def main() -> None:
    """Parse CLI arguments and delegate to ``backtest.reporting.report``.

    Reads ``config/backtest_settings.json`` from the current working directory,
    resolves the run directory, and calls ``report()`` to generate
    ``report/equity_curve.png`` and ``report/metrics.md``.
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

    settings = json.loads(Path("config/backtest_settings.json").read_text())
    run_dir  = Path(settings["runs_root"]) / args.run_id

    if not run_dir.exists():
        raise SystemExit(f"Run directory not found: {run_dir}")

    report(run_dir, settings)
    print(f"report written under {run_dir / 'report'}")


if __name__ == "__main__":
    main()
