"""CLI: re-score the analyst predictive-power scoreboard for an existing run.

Reads ``analyst_evidence`` from the run's ``db.sqlite`` and prices from the
window's golden-cache ``store.sqlite``, calls the same pure scoring function
that the end-of-run reporter uses, and PRINTS the rendered section to stdout.

This entrypoint exists so the scoreboard can be iterated on a formula change
*without* re-running the full backtest — the verdicts and prices are already
on disk.

Usage::

    PYTHONPATH=src python -m scripts.backtest_scoreboard \\
        --run-dir backtests/baseline-2025-09/runs/full-backtest-iter-3 \\
        --window baseline-2025-09

The script does NOT append to ``report/metrics.md``.  Pipe to a file or use
``backtest_report`` (which re-runs the full reporter and appends) if you want
a committed update.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from backtest.cache.store import CachedDataStore
from backtest.scoreboard import build_analyst_scoreboard, render_scoreboard_md
from backtest.settings import cache_path_for_window, get_backtest_settings


def main() -> None:
    """Parse CLI arguments, build the scoreboard, and print the section to stdout.

    Exits with code 1 if the run directory or cache file cannot be located so
    the caller gets a clear error rather than an empty scoreboard silently.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Re-score the analyst predictive-power scoreboard for an existing "
            "backtest run and print the Markdown section to stdout."
        ),
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help=(
            "Path to the run directory (the folder containing db.sqlite), "
            "e.g. backtests/baseline-2025-09/runs/full-backtest-iter-3"
        ),
    )
    parser.add_argument(
        "--window",
        required=True,
        help=(
            "Window key from config/backtest_windows.json "
            "(e.g. baseline-2025-09).  Used to locate the golden-cache "
            "store.sqlite at <backtests_root>/<window>/store.sqlite."
        ),
    )
    parser.add_argument(
        "--neutralise-by",
        choices=["sector", "universe"],
        default=None,
        help=(
            "Override the cross-sectional neutralisation mode for this re-score "
            "only.  Defaults to config's scoreboard_neutralise_by.  Useful for "
            "an A/B comparison (universe vs sector) without editing config."
        ),
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir)

    # Validate that the run directory and its db.sqlite exist.
    db_path = run_dir / "db.sqlite"
    if not db_path.exists():
        print(
            f"ERROR: db.sqlite not found at {db_path}\n"
            f"Make sure --run-dir points to the run's root folder.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Locate the per-window golden cache.
    settings   = get_backtest_settings()
    cache_path = cache_path_for_window(settings, args.window)

    if not cache_path.exists():
        print(
            f"ERROR: Golden cache not found at {cache_path}\n"
            f"Run backtest_fetch for window '{args.window}' first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    horizons = settings.forward_return_horizons_days

    # Open the cache and build the scoreboard.  Inference mode, neutralisation
    # mode and per-analyst primary horizons all come from config — never
    # hardcoded here (config convention).
    cache  = CachedDataStore(cache_path)
    result = build_analyst_scoreboard(
        db_path=db_path,
        cache=cache,
        horizons=horizons,
        primary_horizon_by_analyst=settings.primary_horizon_by_analyst,
        neutralise_by=args.neutralise_by or settings.scoreboard_neutralise_by,
        inference=settings.scoreboard_inference,
    )
    section = render_scoreboard_md(result)

    # Print to stdout — caller can pipe to a file if persistence is wanted.
    print(section)


if __name__ == "__main__":
    main()
