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

Phase 5 data-model split: ``StockStats`` and ``get_stock_stats`` are retired.
Use ``get_price_history`` for OHLCV bars and ``get_company_ratios`` for scalar
fundamentals — they share a single yfinance round-trip per ticker per tick.
"""
from . import providers as _providers  # noqa: F401  — triggers @register decorators


def _validate_active_providers_are_registered() -> None:
    """Validate that every (domain, provider) pair in config/data.json is registered.

    Raises ``RuntimeError`` if any configured pair has no ``@register``-decorated
    function in the provider modules.
    """
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
    CompanyRatios,
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
    PriceHistory,
    ProviderError,
    SocialSentiment,
    SocialSentimentSnapshot,
    StockSignalBundle,
)
from .rate_limit import AsyncRateLimiter
from .registry import dispatch as _dispatch  # noqa: F401  (re-export)
from .registry import min_decision_interval_seconds


async def get_price_history(
    ticker: str, period: str = "1y", interval: str = "1d"
) -> PriceHistory:
    """Fetch OHLCV history for ``ticker`` via the active price-history provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    period:
        yfinance history period (default ``"1y"``).
    interval:
        yfinance history interval (default ``"1d"``).

    Returns
    -------
    PriceHistory
        OHLCV bars ordered oldest -> newest.
    """
    return await _dispatch("price_history", ticker.upper(), period=period, interval=interval)


async def get_company_ratios(
    ticker: str, period: str = "1y", interval: str = "1d"
) -> CompanyRatios:
    """Fetch scalar fundamentals for ``ticker`` via the active ratios provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    period:
        yfinance history period (default ``"1y"``).
    interval:
        yfinance history interval (default ``"1d"``).

    Returns
    -------
    CompanyRatios
        Scalar fundamentals + summary stats.
    """
    return await _dispatch("company_ratios", ticker.upper(), period=period, interval=interval)


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
    # Bundle endpoint
    "get_stock_signal_bundle",
    "get_stock_signal_bundle_blocking",
    # Individual providers (prefer the bundle)
    "get_price_history",
    "get_company_ratios",
    "get_stock_news",
    "get_public_figure_trades",
    "get_insider_trades",
    "get_notable_holders",
    "get_social_sentiment",
    "get_company_filings",
    # Models
    "CompanyRatios",
    "Filing",
    "InsiderTrade",
    "NewsArticle",
    "NotableHolder",
    "OHLCBar",
    "PoliticianTrade",
    "PriceHistory",
    "ProviderError",
    "SocialSentiment",
    "SocialSentimentSnapshot",
    "StockSignalBundle",
    # Rate limits
    "AsyncRateLimiter",
    "min_decision_interval_seconds",
]
