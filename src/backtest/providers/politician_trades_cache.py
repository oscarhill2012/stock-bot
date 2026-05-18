"""Politician-trades cache provider — reads STOCK Act disclosures from the cache store.

Registered as ``("politician_trades", "cache")``.  The live provider fetches
from Quiver Quantitative; this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``COALESCE(disclosure_date, transaction_date)``
(i.e. the date the trade became publicly known), not ``transaction_date`` alone.
The STOCK Act gives US lawmakers up to 45 days between a trade and its
disclosure; using only ``transaction_date`` would expose data a backtest at
``as_of`` could not have seen.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import PoliticianTrade
from data.registry import register


@register(
    "politician_trades", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int,   # required — defaults now flow from get_config() in caller
    **_unused,
) -> list[PoliticianTrade]:
    """Return politician trades disclosed at or before ``as_of``.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound on ``COALESCE(disclosure_date, transaction_date)``.
    lookback_days:
        How many calendar days before ``as_of`` to include.  Required — the
        caller is responsible for supplying the value from ``get_config()``.

    Returns
    -------
    list[PoliticianTrade]
        Matching trades, most-recent by PIT date first.
    """
    return get_store().read_politician_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
