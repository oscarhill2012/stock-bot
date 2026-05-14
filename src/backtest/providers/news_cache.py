"""News cache provider — reads from ``CachedDataStore`` instead of going to network.

Registered as ``upstream="cache"`` so the backtest runner can point the
``news`` domain at this provider by calling
``set_active_provider("news", "cache")``.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import NewsArticle
from data.registry import register


@register(
    "news",
    "cache",
    upstream="cache",
    rate_per_minute=1_000_000,
    burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 30,
    **_unused,
) -> list[NewsArticle]:
    """Return news for ``ticker`` published at or before ``as_of``.

    Parameters
    ----------
    ticker:
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — articles published after this are excluded.
    lookback_days:
        How many days back from ``as_of`` to include (default 30).
    **_unused:
        Absorbs live-provider kwargs (``from_date``, ``to_date``, ``limit``,
        etc.) that the cache provider does not need.

    Returns
    -------
    list[NewsArticle]
        Sorted by ``published_at`` descending (newest first).
    """
    return get_store().read_news(ticker, as_of=as_of, lookback_days=lookback_days)
