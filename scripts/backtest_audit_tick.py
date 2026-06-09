"""CLI: re-run one tick with in-store capture enabled and produce a deep dump.

Usage::

    PYTHONPATH=src python -m scripts.backtest_audit_tick \\
        --run-id svb-stress-2023-03-<sha7> \\
        --window svb-stress-2023-03 \\
        --tick   2023-03-10T09:30:00-05:00 \\
        --phase  open

The script replays a single tick against the existing run's golden cache.
``CachedDataStore._audit_enable_capture()`` is called before the tick runs,
which instructs the store to record every row it returns.  Every row
delivered to any analyst is captured, then the ``upstream_verifier`` checks
each row against its upstream source.  Output is two files under
``<run-dir>/audit/``:

- ``<tick-slug>.full.jsonl`` — one JSON line per (analyst, ticker, row)
- ``<tick-slug>.summary.md`` — human-readable tripwire counts

This is a Layer-2 audit tool, intended for on-demand investigation rather
than routine runs.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

from backtest.audit.deep_dump import build_deep_rows, write_deep_dump
from backtest.cache.store import CachedDataStore
from backtest.driver import Driver, _slug
from backtest.providers._store_handle import set_store
from backtest.runner import Runner
from backtest.schedule import Tick


def main() -> None:
    """CLI entrypoint — re-audit a single tick from a completed run.

    Reads the run directory, enables in-store capture on a plain
    ``CachedDataStore``, replays the single tick through the full pipeline,
    then writes the deep JSONL + summary markdown under ``<run-dir>/audit/``.
    """
    # Strict mode prevents wall-clock leakage through timeguard even inside
    # this replay script.
    os.environ["STOCKBOT_STRICT_AS_OF"] = "1"

    parser = argparse.ArgumentParser(
        description="Replay one backtest tick with deep audit capture.",
    )
    parser.add_argument(
        "--run-id",
        required=True,
        help="Existing run directory under runs_root (e.g. svb-stress-2023-03-abc1234)",
    )
    parser.add_argument(
        "--window",
        required=True,
        help="Window key matching a key in config/backtest_windows.json",
    )
    parser.add_argument(
        "--tick",
        required=True,
        help="ISO timestamp of the tick to replay (e.g. 2023-03-10T09:30:00-05:00)",
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=["open", "close"],
        help="Tick phase",
    )
    args = parser.parse_args()

    # Locate the run directory using the per-window runs root.
    runs_root = Runner._runs_root_from_config(args.window)
    run_dir   = runs_root / args.run_id
    if not run_dir.exists():
        print(f"run dir not found: {run_dir}", file=sys.stderr)
        sys.exit(2)

    # Resolve the per-window cache path — each window has its own SQLite store.
    from backtest.settings import cache_path_for_window, get_backtest_settings
    settings   = get_backtest_settings()
    cache_path = cache_path_for_window(settings, args.window)

    # Build a plain cache store and enable per-tick read capture on it.
    # Plan 10 collapsed the separate decorator into the store itself,
    # so a single API surface drives both the live driver and this CLI.
    store = CachedDataStore(cache_path)
    store._audit_enable_capture()

    # Register the capturing store as the active store for this process.
    # Providers call get_store() to read from it during the tick replay.
    set_store(store)

    tick = Tick(as_of=datetime.fromisoformat(args.tick), phase=args.phase)

    # Replay this single tick through the live pipeline.
    driver = Driver(
        broker=None,   # reads only during audit replay; broker writes are no-ops
        run_dir=run_dir,
        window_key=args.window,
        run_id=args.run_id,
    )

    # Seed minimal state — a proper replay would restore the tick's snapshot
    # from the run's db.sqlite; for v1 we accept the minimal seed.
    # Keys use the canonical namespaced form:
    #   - "user:positions" (the live pipeline reads state["user:positions"])
    #   - "user:thesis"    (the strategist prompt resolves {user:thesis?})
    # The bare "positions" and "thesis" keys are dead and are NOT seeded here.
    state: dict = {
        "watchlist":        [],
        "tickers":          [],
        "portfolio":        {},
        "user:positions":   {},
        "memory_buffer":    [],
        "day_digest":       "",
        "user:thesis":      "",
    }

    asyncio.run(driver.run(state, [tick]))

    # Drain the captured reads and write the deep dump.
    captured = store._audit_drain_reads()
    rows     = build_deep_rows(captured=captured, tick_as_of=tick.as_of)

    audit_dir = run_dir / "audit"
    full, summary = write_deep_dump(
        audit_dir=audit_dir,
        tick_slug=_slug(tick.as_of) + "-" + tick.phase,
        rows=rows,
    )

    print(f"wrote {full}")
    print(f"wrote {summary}")


if __name__ == "__main__":  # pragma: no cover
    main()
