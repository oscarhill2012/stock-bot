"""SEC filings cache provider — reads 10-K/10-Q/8-K filings from the cache store.

Registered as ``("filings", "cache")``.  The live provider fetches from EDGAR;
this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``filed_at`` (SEC filing date, not the fiscal
period the filing covers — a fiscal-year 10-K for FY2022 might be filed in
February 2023; filtering on ``filed_at`` prevents the backtest at e.g. January
2023 from seeing it).
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
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
    lookback_days: int = 365,
    **_unused,
) -> list[Filing]:
    """Return SEC filings filed at or before ``as_of``.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``filed_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include (default 365).

    Returns
    -------
    list[Filing]
        Matching filings, most-recently-filed first.
    """
    return get_store().read_filings(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
