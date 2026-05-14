"""CLI: regenerate the report for an existing backtest run directory.

Usage::

    PYTHONPATH=src python -m scripts.backtest_report --run-id svb-stress-2023-03-abc1234

This is useful after the fact — e.g. if reporting failed mid-run, or if you
want to regenerate with different forward-return horizons after updating
``config/backtest_settings.json``.

All output is written under ``<run_dir>/report/``:
- ``equity_curve.png``   — bot vs SPY normalised to 100 at the first tick.
- ``metrics.md``         — total return, Sharpe, max drawdown, vs-SPY delta.

Decision snapshots in ``<run_dir>/decisions/`` are patched in-place with
``forward_returns``.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


def main() -> None:
    """CLI entrypoint — parse args, load settings, call ``report()``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    )
    logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(
        description="Regenerate the report for an existing backtest run."
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help=(
            "Run ID to generate the report for, e.g. "
            "``svb-stress-2023-03-abc1234``.  Must match a directory under "
            "``<runs_root>`` in ``config/backtest_settings.json``."
        ),
    )
    parser.add_argument(
        "--settings",
        default="config/backtest_settings.json",
        help="Path to the backtest settings file (default: config/backtest_settings.json).",
    )
    args = parser.parse_args()

    # ── Load settings ──────────────────────────────────────────────────────
    settings_path = Path(args.settings)
    if not settings_path.exists():
        logger.error("settings file not found: %s", settings_path)
        sys.exit(1)

    settings: dict = json.loads(settings_path.read_text())

    # ── Resolve run directory ──────────────────────────────────────────────
    runs_root = Path(settings["runs_root"])
    run_dir   = runs_root / args.run_id

    if not run_dir.exists():
        logger.error(
            "run directory not found: %s  (looked under %s)",
            run_dir,
            runs_root,
        )
        sys.exit(1)

    db_path = run_dir / "db.sqlite"
    if not db_path.exists():
        logger.error(
            "db.sqlite not found in %s — has the run completed?", run_dir
        )
        sys.exit(1)

    # ── Generate report ────────────────────────────────────────────────────
    from backtest.reporting import report

    logger.info("generating report for run: %s", args.run_id)
    report(run_dir, settings)

    report_dir = run_dir / "report"
    logger.info("report written under %s", report_dir)
    print(f"report written under {report_dir}")


if __name__ == "__main__":
    main()
