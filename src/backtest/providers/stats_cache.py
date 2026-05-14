"""Stats (market-meta) cache provider — reads from ``CachedDataStore``.

Registered as ``upstream="cache"`` so the backtest runner can point the
``stats`` domain at this provider by calling
``set_active_provider("stats", "cache")``.

The ``period`` / ``interval`` kwargs accepted by the live yfinance provider
are absorbed and ignored; the cache materialises a daily snapshot that
is returned as-is.  The caller (fundamental analyst) that needs historical
bars should query the OHLCV table separately via the aggregator.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import StockStats
from data.registry import register


@register(
    "stats",
    "cache",
    upstream="cache",
    rate_per_minute=1_000_000,
    burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused,
) -> StockStats | None:
    """Return the latest market-meta snapshot at or before ``as_of``.

    Parameters
    ----------
    ticker:
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — the most recent snapshot whose ``as_of_date``
        is ``<= as_of.date()`` is returned.
    period:
        Accepted for live-provider signature compatibility; ignored by cache.
    interval:
        Accepted for live-provider signature compatibility; ignored by cache.
    **_unused:
        Absorbs any other live-provider kwargs.

    Returns
    -------
    StockStats | None
        The most recent snapshot, or ``None`` if no cached data exists for
        the ticker before ``as_of``.
    """
    return get_store().read_market_meta(ticker, as_of=as_of)
