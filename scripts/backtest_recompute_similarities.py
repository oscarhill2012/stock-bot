"""CLI: re-score persisted filing-similarity cosines for one window, no network.

Usage (from project root):

    PYTHONPATH=src python -m scripts.backtest_recompute_similarities --window svb-stress-2023-03
    PYTHONPATH=src python -m scripts.backtest_recompute_similarities --window svb-stress-2023-03 \\
        --watchlist config/watchlist.json

Companion to ``scripts/backtest_fetch.py`` — but where that script re-hits
EDGAR, this one touches NO network at all.  It exists for the case where the
filing *text* already cached is correct but the *scoring rule* that turns
that text into persisted cosines has changed (e.g. the v1.1 stub guard added
in ``agents.analysts.fundamental.fetch.compute_filing_similarities``).

For each ticker in the watchlist:

1. Read the ticker's full filing pool back from the golden cache
   (``store.read_filings``) — every cached filing, not windowed by date;
   the similarity precompute needs the whole pool to find prior-year pairs.
2. Re-run ``compute_filing_similarities`` over that pool.  Its output is
   authoritative (all six similarity fields explicitly set, score or
   ``None``) — see the function's docstring for why that matters for a
   recompute specifically.
3. Persist the result with ``store.update_filing_similarities`` (an UPDATE
   keyed on ``accession_no``, unlike the INSERT-only ``write_filings`` — this
   is the only way to clear a stale cosine to NULL on an existing row).

Prints a per-ticker and total summary of cosines written (non-null after
recompute) vs cleared to NULL (were non-null, are now None) so an operator
can see the guard's effect on the cache at a glance.
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.stock_picker import normalise_to_symbols

_logger = logging.getLogger(__name__)

# Upper bound passed to ``read_filings`` — recompute wants the ticker's
# WHOLE cached filing pool (it re-derives prior-year pairs itself), so this
# is deliberately far in the future rather than the window's actual end
# date.  Cached filings are always historical, so this never excludes rows.
_FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)

# The six similarity scalar fields on ``Filing`` that this script re-scores.
# Kept in one place so the "written vs cleared" tally below and the
# store-level column list cannot silently drift apart.
_SIMILARITY_FIELD_NAMES = (
    "mda_cosine_vs_prior", "mda_jaccard_vs_prior",
    "risk_cosine_vs_prior", "risk_jaccard_vs_prior",
    "litigation_cosine_vs_prior", "litigation_jaccard_vs_prior",
)


def recompute_window_similarities(store, watchlist: list[str]) -> dict[str, dict[str, int]]:
    """Re-score every ticker's cached filing similarities in place.

    Pure of I/O beyond the store itself — no network calls are made, which
    is what makes this safe to run repeatedly and cheap compared to a full
    EDGAR refetch.

    Parameters
    ----------
    store:
        An open ``CachedDataStore`` for the window being recomputed.
    watchlist:
        Ticker symbols to recompute (the window's watchlist).

    Returns
    -------
    dict[str, dict[str, int]]
        Per-ticker ``{"written": int, "cleared": int, "updated_rows": int}``
        plus a ``"_total"`` key with the summed counts across tickers.
    """
    # Function-local import — mirrors the fetcher's own deferred import of
    # this module (see ``backtest.cache.fetcher``): importing
    # ``agents.analysts.fundamental.fetch`` eagerly drags in the whole
    # google-adk stack, which this low-level recompute script does not need.
    from agents.analysts.fundamental.fetch import compute_filing_similarities

    summary: dict[str, dict[str, int]] = {}
    total_written = 0
    total_cleared = 0
    total_updated_rows = 0

    for ticker in watchlist:
        pool = store.read_filings(ticker, as_of=_FAR_FUTURE)
        if not pool:
            _logger.info("recompute_window_similarities: %s — no cached filings, skipping", ticker)
            summary[ticker] = {"written": 0, "cleared": 0, "updated_rows": 0}
            continue

        # Snapshot the six fields BEFORE recompute so the "cleared" tally
        # below can tell a fresh score apart from a stale value being wiped.
        before = {f.accession_no: {name: getattr(f, name) for name in _SIMILARITY_FIELD_NAMES} for f in pool}

        rescored = compute_filing_similarities(pool)

        written = 0
        cleared = 0
        for f in rescored:
            prior_values = before[f.accession_no]
            for name in _SIMILARITY_FIELD_NAMES:
                new_value = getattr(f, name)
                if new_value is not None:
                    written += 1
                elif prior_values[name] is not None:
                    # Was non-null before, is None now — the stub guard (or
                    # any other recompute-time change) cleared a stale value.
                    cleared += 1

        updated_rows = store.update_filing_similarities(rescored)

        _logger.info(
            "recompute_window_similarities: %s — %d filing(s), %d field(s) written, "
            "%d field(s) cleared to NULL, %d row(s) updated",
            ticker, len(pool), written, cleared, updated_rows,
        )

        summary[ticker] = {"written": written, "cleared": cleared, "updated_rows": updated_rows}
        total_written += written
        total_cleared += cleared
        total_updated_rows += updated_rows

    summary["_total"] = {
        "written": total_written, "cleared": total_cleared, "updated_rows": total_updated_rows,
    }
    return summary


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse CLI arguments and run the offline similarity recompute."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Re-score persisted filing-similarity cosines for one window's "
            "cached filings, in place, with no network access — for use "
            "after a scoring-rule change (e.g. the v1.1 stub guard) where "
            "the cached filing text is still valid and only the persisted "
            "cosines need refreshing."
        )
    )
    parser.add_argument(
        "--window",
        required=True,
        help="Window key from config/backtest_windows.json (e.g. svb-stress-2023-03).",
    )
    parser.add_argument(
        "--watchlist",
        default="config/watchlist.json",
        help="Path to a JSON file with a 'tickers' list (default: config/watchlist.json).",
    )
    args = parser.parse_args()

    from backtest.cache.store import CachedDataStore
    from backtest.settings import cache_path_for_window, get_backtest_settings
    from backtest.windows import load_windows

    # Validate the window key against the registered windows file — same
    # loud-failure behaviour as backtest_fetch.py — even though the window's
    # date range itself is not used below (recompute re-derives prior-year
    # pairs from the cached pool, not from the window bounds).
    windows = load_windows(Path("config/backtest_windows.json"))
    if args.window not in windows:
        raise SystemExit(
            f"Unknown window key '{args.window}'. Available: {sorted(windows)}"
        )

    settings = get_backtest_settings()
    cache_path = cache_path_for_window(settings, args.window)
    if not cache_path.exists():
        raise SystemExit(
            f"No cache found at {cache_path} for window '{args.window}' — "
            "run scripts.backtest_fetch first."
        )

    watchlist_path = Path(args.watchlist)
    watchlist = normalise_to_symbols(json.loads(watchlist_path.read_text())["tickers"])

    store = CachedDataStore(cache_path)

    _logger.info(
        "Starting similarity recompute: window=%s tickers=%d cache=%s",
        args.window, len(watchlist), cache_path,
    )

    summary = recompute_window_similarities(store, watchlist)

    total = summary["_total"]
    _logger.info(
        "Recompute complete: %d field(s) written, %d field(s) cleared to NULL, "
        "%d row(s) updated across %d ticker(s).",
        total["written"], total["cleared"], total["updated_rows"], len(watchlist),
    )


if __name__ == "__main__":
    main()
