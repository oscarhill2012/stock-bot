"""CLI: re-score the analyst predictive-power scoreboard for existing run(s).

Reads ``analyst_evidence`` from each run's ``db.sqlite`` and prices from the
window's golden-cache ``store.sqlite``, calls the same pure scoring function
that the end-of-run reporter uses, and PRINTS the rendered section to stdout.

This entrypoint exists so the scoreboard can be iterated on a formula change
*without* re-running the full backtest — the verdicts and prices are already
on disk.

Single-window usage (unchanged)::

    PYTHONPATH=src python -m scripts.backtest_scoreboard \\
        --run-dir backtests/baseline-2025-09/runs/full-backtest-iter-3 \\
        --window baseline-2025-09

Multi-window pooling — repeat ``--run-dir`` / ``--window`` as a positional
pair per window.  Prints each window's own scoreboard AND a pooled scoreboard
(observations from every window aggregated together, clustered by
``(ticker, window)`` so two regimes months apart are not treated as one
autocorrelated cluster)::

    PYTHONPATH=src python -m scripts.backtest_scoreboard \\
        --run-dir backtests/baseline-2025-09/runs/analysts-eval-iter-11 \\
        --window baseline-2025-09 \\
        --run-dir backtests/iran-conflict-2026-02/runs/analysts-eval-iter-11 \\
        --window iran-conflict-2026-02

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
from backtest.scoreboard import (
    build_analyst_scoreboard,
    build_pooled_analyst_scoreboard,
    render_scoreboard_md,
)
from backtest.settings import cache_path_for_window, get_backtest_settings


def _pair_run_specs(
    *,
    run_dirs: list[str],
    windows: list[str],
) -> list[tuple[Path, str]]:
    """Pair repeated ``--run-dir`` / ``--window`` flags by position.

    The two flags are collected independently by ``argparse`` (``action=
    "append"``), so the i-th run directory belongs with the i-th window.  A
    length mismatch is a user error — the caller forgot a flag — and must fail
    loudly rather than silently truncate to the shorter list (silent
    degradation is this project's recurring bug class).

    Parameters
    ----------
    run_dirs:
        Run-directory paths in the order the ``--run-dir`` flags appeared.
    windows:
        Window keys in the order the ``--window`` flags appeared.

    Returns
    -------
    list[tuple[Path, str]]
        ``(run_dir_path, window_key)`` pairs, in flag order.

    Raises
    ------
    ValueError
        If the two lists differ in length.
    """

    if len(run_dirs) != len(windows):
        raise ValueError(
            f"--run-dir and --window must be supplied the same number of "
            f"times (got {len(run_dirs)} run-dir(s) and {len(windows)} "
            f"window(s)); supply them as positional pairs, one --window per "
            f"--run-dir."
        )

    return [(Path(run_dir), window) for run_dir, window in zip(run_dirs, windows)]


def _resolve_window(run_dir: Path, window: str, settings) -> tuple[Path, CachedDataStore]:
    """Locate and open the evidence DB + golden cache for one window.

    Validates that both the run's ``db.sqlite`` and the per-window golden
    cache exist, exiting with code 1 and a clear stderr message if either is
    missing (so the caller never gets a silently-empty scoreboard).

    Parameters
    ----------
    run_dir:
        Path to the run directory (the folder containing ``db.sqlite``).
    window:
        Window key used to locate the golden cache via config.
    settings:
        Loaded ``BacktestSettings`` (passed in so it is read once per run).

    Returns
    -------
    tuple[Path, CachedDataStore]
        The evidence ``db.sqlite`` path and an open cache store.
    """

    db_path = run_dir / "db.sqlite"
    if not db_path.exists():
        print(
            f"ERROR: db.sqlite not found at {db_path}\n"
            f"Make sure --run-dir points to the run's root folder.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    cache_path = cache_path_for_window(settings, window)
    if not cache_path.exists():
        print(
            f"ERROR: Golden cache not found at {cache_path}\n"
            f"Run backtest_fetch for window '{window}' first.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return db_path, CachedDataStore(cache_path)


def main(argv: list[str] | None = None) -> None:
    """Parse CLI arguments, build the scoreboard(s), and print to stdout.

    Accepts one or more ``(--run-dir, --window)`` pairs.  With a single pair
    the behaviour is identical to the legacy single-window re-score.  With two
    or more pairs, each window's own scoreboard is printed first, followed by a
    pooled scoreboard aggregating every window's observations together.

    Parameters
    ----------
    argv:
        Optional argument vector (for testing); defaults to ``sys.argv[1:]``.

    Exits with code 1 if any run directory or cache file cannot be located so
    the caller gets a clear error rather than an empty scoreboard silently.
    """

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Re-score the analyst predictive-power scoreboard for existing "
            "backtest run(s) and print the Markdown section(s) to stdout.  "
            "Repeat --run-dir/--window to pool multiple windows."
        ),
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        action="append",
        help=(
            "Path to a run directory (the folder containing db.sqlite), "
            "e.g. backtests/baseline-2025-09/runs/full-backtest-iter-3.  "
            "Repeat once per window to pool multiple runs."
        ),
    )
    parser.add_argument(
        "--window",
        required=True,
        action="append",
        help=(
            "Window key from config/backtest_windows.json "
            "(e.g. baseline-2025-09).  Used to locate the golden-cache "
            "store.sqlite.  Supply one --window per --run-dir, in the same "
            "order."
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
    args = parser.parse_args(argv)

    # Pair the repeated flags by position (raises loudly on a count mismatch).
    pairs = _pair_run_specs(run_dirs=args.run_dir, windows=args.window)

    # Inference mode, neutralisation mode and per-analyst primary horizons all
    # come from config — never hardcoded here (config convention).
    settings      = get_backtest_settings()
    horizons      = settings.forward_return_horizons_days
    neutralise_by = args.neutralise_by or settings.scoreboard_neutralise_by

    # ── Single-window: identical to the legacy re-score path ─────────────────
    if len(pairs) == 1:
        run_dir, window = pairs[0]
        db_path, cache  = _resolve_window(run_dir, window, settings)

        result = build_analyst_scoreboard(
            db_path=db_path,
            cache=cache,
            horizons=horizons,
            primary_horizon_by_analyst=settings.primary_horizon_by_analyst,
            neutralise_by=neutralise_by,
            inference=settings.scoreboard_inference,
        )
        print(render_scoreboard_md(result))
        return

    # ── Multi-window: per-window sections first, then the pooled section ─────
    #
    # Each window is scored in isolation (its own dedup + within-window peer
    # means) for the per-window view; the same opened caches are then handed to
    # the pooling builder, which aggregates every window's observations once,
    # clustered by (ticker, window).  Per-window views are kept alongside the
    # pool because pooling blends regimes and can cancel a real regime-specific
    # effect into noise.
    resolved: list[tuple[Path, CachedDataStore, str]] = []
    for run_dir, window in pairs:
        db_path, cache = _resolve_window(run_dir, window, settings)
        resolved.append((db_path, cache, window))

    for db_path, cache, window in resolved:
        result = build_analyst_scoreboard(
            db_path=db_path,
            cache=cache,
            horizons=horizons,
            primary_horizon_by_analyst=settings.primary_horizon_by_analyst,
            neutralise_by=neutralise_by,
            inference=settings.scoreboard_inference,
        )
        print(f"\n===== WINDOW: {window} (neutralise_by={neutralise_by}) =====\n")
        print(render_scoreboard_md(result))

    pooled = build_pooled_analyst_scoreboard(
        runs=resolved,
        horizons=horizons,
        primary_horizon_by_analyst=settings.primary_horizon_by_analyst,
        neutralise_by=neutralise_by,
        inference=settings.scoreboard_inference,
    )
    window_keys = "+".join(window for _, _, window in resolved)
    print(
        f"\n===== POOLED: {window_keys} "
        f"(neutralise_by={neutralise_by}, clustered by (ticker, window)) =====\n"
    )
    print(render_scoreboard_md(pooled))


if __name__ == "__main__":
    main()
