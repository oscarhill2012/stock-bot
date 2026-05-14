"""Politician-trades cache provider — reads from ``CachedDataStore``.

Registered as ``upstream="cache"`` so the backtest runner can point the
``politician_trades`` domain at this provider by calling
``set_active_provider("politician_trades", "cache")``.

Point-in-time correctness: the store filters on
``COALESCE(disclosure_date, transaction_date)``.  The public only learns of
a trade when it is disclosed (up to 45 days after the transaction under the
STOCK Act); using ``transaction_date`` alone would leak future information.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import PoliticianTrade
from data.registry import register


@register(
    "politician_trades",
    "cache",
    upstream="cache",
    rate_per_minute=1_000_000,
    burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[PoliticianTrade]:
    """Return politician trades disclosed at or before ``as_of``.

    Parameters
    ----------
    ticker:
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — trades not yet publicly disclosed are excluded.
    lookback_days:
        How many days back from ``as_of`` to include (default 90).
    **_unused:
        Absorbs live-provider kwargs the cache provider does not need.

    Returns
    -------
    list[PoliticianTrade]
        Sorted by disclosure date descending (most recently disclosed first).
    """
    return get_store().read_politician_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
