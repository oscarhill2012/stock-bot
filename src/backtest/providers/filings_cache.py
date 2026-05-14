"""SEC filings cache provider — reads from ``CachedDataStore``.

Registered as ``upstream="cache"`` so the backtest runner can point the
``filings`` domain at this provider by calling
``set_active_provider("filings", "cache")``.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import Filing
from data.registry import register


@register(
    "filings",
    "cache",
    upstream="cache",
    rate_per_minute=1_000_000,
    burst=1_000,
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
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — filings after this are excluded.
    lookback_days:
        How many days back from ``as_of`` to include (default 365).
    **_unused:
        Absorbs live-provider kwargs the cache provider does not need.

    Returns
    -------
    list[Filing]
        Sorted by ``filed_at`` descending (most recent first).
    """
    return get_store().read_filings(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
