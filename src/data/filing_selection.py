"""Shared analyst-visibility rule for SEC filings (2026-06-11 redesign).

This module is the single source of truth for *which* filings an analyst
sees at a given moment.  Both read paths apply it:

- the live EDGAR provider (``data/providers/filings/edgar.py``), after
  fetching its per-form candidates;
- the backtest cache provider (``backtest/providers/filings_cache.py``),
  after reading every cached row at or before the tick.

Keeping the rule in one pure function means live and replay cannot drift
apart — the contract test feeds both paths the same synthetic filings and
asserts identical output.

The rule itself:

- **latest 10-K** with ``filed_at <= as_of`` — the current annual report.
- **latest 10-Q** with ``filed_at <= as_of`` — the current quarterly report.
- **every 8-K** filed within ``staleness_days`` of ``as_of`` — events decay,
  so the horizon (``filings_8k_staleness_days`` in ``config/data.json``) is
  the one genuine tuning knob.

Periodic forms carry **no** staleness bound: an eleven-month-old 10-K *is*
the current annual report, and binning it would starve the fundamentals
anchor for exactly the quiet tickers the rule exists to protect.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Iterable

from data.models import Filing

# Forms whose *latest single instance* is analyst-visible — older instances
# are superseded by definition (a newer annual/quarterly report replaces the
# previous one entirely).
_PERIODIC_FORMS: tuple[str, ...] = ("10-K", "10-Q")

# Event form bounded by the staleness horizon rather than supersession.
_EVENT_FORM = "8-K"


def select_current_filings(
    filings: Iterable[Filing],
    *,
    as_of: datetime,
    staleness_days: int,
) -> list[Filing]:
    """Select the analyst-visible filings from ``filings`` as of ``as_of``.

    Parameters
    ----------
    filings:
        Candidate filings, any order, any form types.  Rows with
        ``filed_at > as_of`` are invisible (PIT correctness); form types
        other than 10-K / 10-Q / 8-K are dropped.
    as_of:
        The simulation or wall clock — upper bound (inclusive) on
        ``filed_at``.
    staleness_days:
        Horizon for 8-K event filings.  An 8-K filed exactly
        ``staleness_days`` before ``as_of`` is still visible (inclusive
        boundary).

    Returns
    -------
    list[Filing]
        At most one 10-K, at most one 10-Q, and every fresh 8-K — ordered
        most-recently-filed first.  Ties on ``filed_at`` break on
        ``accession_no`` so the selection is deterministic.
    """
    # PIT gate first — nothing filed after the clock exists yet.
    visible = [f for f in filings if f.filed_at <= as_of]

    selected: list[Filing] = []

    # ── Periodic anchors: the single latest instance per form ──────────────
    for form in _PERIODIC_FORMS:
        candidates = [f for f in visible if f.form_type == form]
        if candidates:
            selected.append(
                max(candidates, key=lambda f: (f.filed_at, f.accession_no))
            )

    # ── Event filings: every 8-K within the staleness horizon ──────────────
    horizon = as_of - timedelta(days=staleness_days)
    selected.extend(
        f for f in visible
        if f.form_type == _EVENT_FORM and f.filed_at >= horizon
    )

    # Newest first, deterministic tie-break — matches the cache read order.
    selected.sort(key=lambda f: (f.filed_at, f.accession_no), reverse=True)
    return selected
