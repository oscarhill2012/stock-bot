"""Data-source layer for StockBot.

Public surface for agents:

    from data import get_stock_signal_bundle, MIN_DECISION_INTERVAL_SECONDS

    bundle = await get_stock_signal_bundle("AAPL")
    # bundle.min_decision_interval_seconds == MIN_DECISION_INTERVAL_SECONDS
    # — do not re-decide for this ticker faster than that.

Per `docs/data-sources.md`, agents should not import provider modules
directly — go through `get_stock_signal_bundle` so the orchestrator
can swap real calls for cached fixtures during tests.

# Rate-limit budgets

Each data source has a token-bucket limiter sized to its free-tier
cap. A coroutine that asks for a token while the bucket is empty
**waits** — it doesn't error. That gives us "free" back-pressure but
also means the slowest source dictates the trading cadence.

| Source   | Budget           | Min interval per call |
|----------|------------------|------------------------|
| Finnhub  | 60 / min         | 1 s                    |
| Quiver   | 30 / min         | 2 s                    |
| yfinance | 60 / min (self)  | 1 s                    |
| EDGAR    | 600 / min (10/s) | 0.1 s                  |

`MIN_DECISION_INTERVAL_SECONDS` exposes the slowest of these. With
edgartools direct EDGAR access (free, 10 req/sec), the floor is now
~2 s (Quiver) — no longer the SEC. The strategist agent should still
treat it as the data-refresh floor: re-deciding faster means churning
on stale signals.
"""
from .aggregator import get_stock_signal_bundle, get_stock_signal_bundle_blocking
from .models import (
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
    ProviderError,
    SocialSentiment,
    SocialSentimentSnapshot,
    StockSignalBundle,
    StockStats,
)
from .providers import (
    get_company_filings,
    get_insider_trades,
    get_notable_holders,
    get_public_figure_trades,
)
from .rate_limit import (
    ALL_LIMITERS,
    EDGAR,
    FINNHUB,
    QUIVER,
    YFINANCE,
    AsyncRateLimiter,
    slowest_min_interval_seconds,
)
from .registry import dispatch as _dispatch
from .settings import ProviderConfigError, get_settings

# The data-refresh floor for a complete bundle. The strategist agent
# must not re-decide for a given ticker faster than this.
MIN_DECISION_INTERVAL_SECONDS: float = slowest_min_interval_seconds(
    FINNHUB, QUIVER, EDGAR, YFINANCE
)


async def get_stock_stats(ticker: str, period: str = "1y", interval: str = "1d"):
    """Fetch OHLCV + fundamentals for `ticker` via the active stats provider."""
    return await _dispatch("stats", ticker.upper(), period=period, interval=interval)


async def get_stock_news(
    ticker: str,
    from_date=None,
    to_date=None,
    *,
    limit: int | None = 50,
):
    """Fetch news articles for `ticker` via the active news provider."""
    from datetime import date as _d
    from datetime import timedelta as _td
    today = _d.today()
    return await _dispatch(
        "news",
        ticker.upper(),
        from_date=from_date or (today - _td(days=7)),
        to_date=to_date or today,
        limit=limit,
    )


async def get_social_sentiment(ticker: str):
    """Fetch social-sentiment snapshot for `ticker` via the active provider."""
    return await _dispatch("social_sentiment", ticker.upper())


__all__ = [
    # Endpoints
    "get_stock_signal_bundle",
    "get_stock_signal_bundle_blocking",
    # Individual providers (prefer the bundle)
    "get_stock_news",
    "get_stock_stats",
    "get_public_figure_trades",
    "get_insider_trades",
    "get_notable_holders",
    "get_social_sentiment",
    "get_company_filings",
    # Models
    "Filing",
    "InsiderTrade",
    "NewsArticle",
    "NotableHolder",
    "OHLCBar",
    "PoliticianTrade",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    "StockStats",
    # Config + rate limits
    "ProviderConfigError",
    "get_settings",
    "AsyncRateLimiter",
    "EDGAR",
    "FINNHUB",
    "QUIVER",
    "YFINANCE",
    "ALL_LIMITERS",
    "slowest_min_interval_seconds",
    "MIN_DECISION_INTERVAL_SECONDS",
]
