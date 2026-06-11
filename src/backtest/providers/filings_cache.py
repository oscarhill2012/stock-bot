"""SEC filings cache provider — reads 10-K/10-Q/8-K filings from the cache store.

Registered as ``("filings", "cache")``.  The live provider fetches from EDGAR;
this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``filed_at`` (SEC filing date, not the fiscal
period the filing covers — a fiscal-year 10-K for FY2022 might be filed in
February 2023; filtering on ``filed_at`` prevents the backtest at e.g. January
2023 from seeing it).

2026-06-11 redesign: the cache holds the raw backfill superset (superseded
periodic filings, stale 8-Ks), and this provider applies the shared
``select_current_filings`` rule per tick — exactly the rule the live EDGAR
provider applies — so replay serves the same selection live would.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.filing_selection import select_current_filings
from data.models import Filing
from data.registry import register


@register(
    "filings", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    staleness_days: int,   # required — defaults flow from get_config() in caller
    **_unused,
) -> list[Filing]:
    """Return the analyst-visible filings selection as of ``as_of``.

    Reads every cached filing at or before ``as_of`` (the store applies the
    PIT filter, unbounded below) and applies the shared analyst-visibility
    rule: latest 10-K, latest 10-Q, and every 8-K within ``staleness_days``.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``filed_at``.
    staleness_days:
        8-K visibility horizon for the selection rule.  Required — the
        caller is responsible for supplying the value from ``get_config()``.

    Returns
    -------
    list[Filing]
        The selected filings, most-recently-filed first.
    """
    cached = get_store().read_filings(ticker, as_of=as_of)

    return select_current_filings(
        cached, as_of=as_of, staleness_days=staleness_days,
    )
