# ruff: noqa: E402  — imports after _validate_active_providers_are_registered() are intentional
"""Data-source layer for StockBot.

Public surface for agents:

    from data import get_stock_signal_bundle, min_decision_interval_seconds

    bundle = await get_stock_signal_bundle("AAPL")
    # bundle.min_decision_interval_seconds == min_decision_interval_seconds()
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

`min_decision_interval_seconds()` exposes the slowest of these. With
edgartools direct EDGAR access (free, 10 req/sec), the floor is now
~2 s (Quiver) — no longer the SEC. The strategist agent should still
treat it as the data-refresh floor: re-deciding faster means churning
on stale signals.
"""
from . import providers as _providers  # noqa: F401  — triggers @register decorators


def _validate_active_providers_are_registered() -> None:
    from .config import get_config
    from .registry import _REGISTRY

    cfg = get_config()
    missing = [(d, n) for d, n in cfg.providers.items() if (d, n) not in _REGISTRY]
    if missing:
        raise RuntimeError(
            f"config/data.json references unregistered (domain, provider) pairs: {missing}"
        )


_validate_active_providers_are_registered()

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
from .rate_limit import AsyncRateLimiter
from .registry import dispatch as _dispatch  # noqa: F401  (re-export)
from .registry import min_decision_interval_seconds


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


async def get_insider_trades(ticker: str, *, lookback_days: int = 30):
    """Fetch SEC Form 4 insider trades for `ticker` via the active provider."""
    return await _dispatch("insider_trades", ticker.upper(), lookback_days=lookback_days)


async def get_public_figure_trades(
    ticker: str | None = None,
    *,
    lookback_days: int = 90,
):
    """Fetch politician/congressional trades via the active provider."""
    return await _dispatch(
        "politician_trades",
        ticker.upper() if ticker else None,
        lookback_days=lookback_days,
    )


async def get_notable_holders(
    ticker: str,
    *,
    lookback_days: int = 180,
    limit: int = 20,
):
    """Fetch notable EDGAR 13F holders for `ticker` via the active provider."""
    return await _dispatch(
        "notable_holders", ticker.upper(),
        lookback_days=lookback_days, limit=limit,
    )


async def get_company_filings(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    include_excerpts: bool = True,
):
    """Fetch SEC filings for `ticker` via the active filings provider."""
    return await _dispatch(
        "filings", ticker.upper(),
        form_types=form_types, limit=limit, include_excerpts=include_excerpts,
    )


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
    # Rate limits
    "AsyncRateLimiter",
    "min_decision_interval_seconds",
]
