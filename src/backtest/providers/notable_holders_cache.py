"""Notable-holders cache provider — reads 13D/13G/13F filings from the cache store.

Registered as ``("notable_holders", "cache")``.  The live provider fetches from
EDGAR; this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``filed_at`` (date SEC received the filing).
A 13D/G filing can describe a position that was accumulated over months; only
the filing date bounds what a backtest at ``as_of`` could actually know.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import NotableHolder
from data.registry import register


@register(
    "notable_holders", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int,   # required — defaults now flow from get_config() in caller
    **_unused,
) -> list[NotableHolder]:
    """Return notable-holder filings at or before ``as_of``.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``filed_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include.  Required — the
        caller is responsible for supplying the value from ``get_config()``.

    Returns
    -------
    list[NotableHolder]
        Matching filings, most-recently-filed first.
    """
    return get_store().read_notable_holders(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
