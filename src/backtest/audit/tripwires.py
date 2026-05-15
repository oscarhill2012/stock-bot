"""Tripwire flags — five boolean checks rolled up per tick.

Each flag is computed from the per-domain summary built by ``telemetry``.
They are the headline diagnostic: the reviewer reads ``SUMMARY.md`` first
and only consults the per-row JSONL when a tripwire fires.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def compute_tripwires(
    *,
    as_of:                     datetime,
    phase:                     str,
    per_domain:                dict[str, dict[str, Any]],
    wall_clock_fallback_fired: bool,
) -> dict[str, bool]:
    """Roll the per-domain summary up into five boolean leak flags.

    Parameters
    ----------
    as_of:
        The tick's historical clock value.
    phase:
        ``"open"`` or ``"close"``.  Determines whether a same-day OHLCV
        bar counts as a leak.
    per_domain:
        Per-domain ``ticker_rows`` summary.  See ``telemetry`` for shape.
    wall_clock_fallback_fired:
        ``True`` iff ``timeguard.resolve_as_of`` returned a wall-clock
        substitute during this tick.  Captured by the strict-mode hook
        (Task 2).

    Returns
    -------
    dict[str, bool]
        Five named tripwire flags.
    """
    any_filter_key_after_as_of    = False
    open_tick_sameday_bar         = False
    midnight_utc_timestamps_seen  = False
    missing_timestamp_rows_seen   = False

    as_of_iso = as_of.isoformat()

    for _domain_name, domain_summary in per_domain.items():
        ticker_rows = domain_summary.get("ticker_rows", {})

        for _ticker, row_summary in ticker_rows.items():
            # Find this domain's max filter-key value and compare to as_of.
            # Domains use different field names — pick the one that matches.
            max_key = (
                row_summary.get("max_published_at")
                or row_summary.get("max_filed_at")
                or row_summary.get("max_ts")
                or row_summary.get("max_disclosure_at")
            )
            if max_key and max_key > as_of_iso:
                any_filter_key_after_as_of = True

            # OHLCV-specific: same-day bar at open is a leak.
            if (
                phase == "open"
                and row_summary.get("sameday_bar_seen") is True
            ):
                open_tick_sameday_bar = True

            # Midnight-UTC count flag.
            if row_summary.get("midnight_utc_count", 0) > 0:
                midnight_utc_timestamps_seen = True

            # Missing-timestamp marker count.
            if row_summary.get("missing_timestamp_count", 0) > 0:
                missing_timestamp_rows_seen = True

    return {
        "wall_clock_fallback_fired":    wall_clock_fallback_fired,
        "any_filter_key_after_as_of":   any_filter_key_after_as_of,
        "open_tick_sameday_bar":        open_tick_sameday_bar,
        "midnight_utc_timestamps_seen": midnight_utc_timestamps_seen,
        "missing_timestamp_rows_seen":  missing_timestamp_rows_seen,
    }
