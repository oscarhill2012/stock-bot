"""News provider that reads from the cache store instead of going to network.

Registered as ``("news", "cache")`` so the backtest runner can call
``set_active_provider("news", "cache")`` to redirect all news fetches
to the local SQLite cache for the duration of a run.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import NewsArticle
from data.registry import register


@register("news", "cache", upstream="cache", rate_per_minute=1_000_000, burst=1_000)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 30,
    **_unused,
) -> list[NewsArticle]:
    """Return news articles for ``ticker`` published at or before ``as_of``.

    The PIT filter is applied by the store: articles whose ``published_at``
    exceeds ``as_of`` are never returned, preventing lookahead bias.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``published_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include (default 30).

    Returns
    -------
    list[NewsArticle]
        Matching articles, most-recent first.
    """
    return get_store().read_news(ticker, as_of=as_of, lookback_days=lookback_days)
