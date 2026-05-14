"""Insider-trades cache provider — reads from ``CachedDataStore``.

Registered as ``upstream="cache"`` so the backtest runner can point the
``insider_trades`` domain at this provider by calling
``set_active_provider("insider_trades", "cache")``.

Point-in-time correctness: the store filters on ``filed_at``, not
``transaction_date``.  Form 4 trades can be transacted days before filing;
using ``transaction_date`` would leak future information into the analysts.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import InsiderTrade
from data.registry import register


@register(
    "insider_trades",
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
) -> list[InsiderTrade]:
    """Return insider trades filed at or before ``as_of``.

    Parameters
    ----------
    ticker:
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — trades filed after this are excluded.
    lookback_days:
        How many days back from ``as_of`` to include (default 90).
    **_unused:
        Absorbs live-provider kwargs the cache provider does not need.

    Returns
    -------
    list[InsiderTrade]
        Sorted by ``filed_at`` descending (most recent first).
    """
    return get_store().read_insider_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
