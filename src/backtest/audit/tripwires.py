"""Tripwire flags — five boolean checks rolled up per tick.

Each flag is computed from the per-domain summary built by ``telemetry``.
They are the headline diagnostic: the reviewer reads ``SUMMARY.md`` first
and only consults the per-row JSONL when a tripwire fires.

Advisory tripwires
------------------
Two keys carry the ``_advisory`` suffix, which signals that they fire
benignly by design and must NOT be treated as evidence of a data leak:

- ``midnight_utc_timestamps_seen_advisory``: date-only data sources
  store timestamps as midnight UTC; every tick on such a source fires
  this tripwire.  It is steady-state, not an anomaly.

- ``open_tick_sameday_bar_advisory``: the raw store query uses an
  inclusive date range (``end=as_of.date()``), so the same-day OHLCV
  bar is visible at the audit layer before ``price_history_cache.fetch``
  strips it.  The analyst never sees the bar; the tripwire reflects the
  store boundary, not a leak.

Operational tooling that counts "fired" tripwires should use
``ACTIONABLE_TRIPWIRES`` to filter the advisory keys out.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Public contract
# ---------------------------------------------------------------------------

#: The subset of tripwire keys that indicate a real, actionable data-leak
#: or clock anomaly.  The ``*_advisory`` keys are intentionally excluded —
#: they fire on every relevant tick by design and carry no signal.
ACTIONABLE_TRIPWIRES: frozenset[str] = frozenset({
    "wall_clock_fallback_fired",
    "any_filter_key_after_as_of",
    "missing_timestamp_rows_seen",
})


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
    any_filter_key_after_as_of         = False
    open_tick_sameday_bar_advisory     = False   # see module docstring
    midnight_utc_timestamps_seen_advisory = False  # see module docstring
    missing_timestamp_rows_seen        = False

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

            # OHLCV-specific: same-day bar visible at the store boundary during
            # open phase.  Advisory only — price_history_cache strips it before
            # any analyst receives it (see module docstring).
            if (
                phase == "open"
                and row_summary.get("sameday_bar_seen") is True
            ):
                open_tick_sameday_bar_advisory = True

            # Midnight-UTC timestamps from date-only sources — advisory only,
            # as midnight promotion is the documented steady-state behaviour
            # for those providers (see module docstring).
            if row_summary.get("midnight_utc_count", 0) > 0:
                midnight_utc_timestamps_seen_advisory = True

            # Missing-timestamp marker count.
            if row_summary.get("missing_timestamp_count", 0) > 0:
                missing_timestamp_rows_seen = True

    return {
        # --- Actionable tripwires (real signal) ----------------------------
        "wall_clock_fallback_fired":              wall_clock_fallback_fired,
        "any_filter_key_after_as_of":             any_filter_key_after_as_of,
        "missing_timestamp_rows_seen":            missing_timestamp_rows_seen,

        # --- Advisory tripwires (benign by design; excluded from counts) ---
        "open_tick_sameday_bar_advisory":         open_tick_sameday_bar_advisory,
        "midnight_utc_timestamps_seen_advisory":  midnight_utc_timestamps_seen_advisory,
    }
