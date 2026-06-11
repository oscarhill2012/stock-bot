# ruff: noqa: E402  — imports after _validate_active_providers_are_registered() are intentional
"""Data-source layer for StockBot.

Public surface for agents:

    from data import get_price_history, get_company_ratios, min_decision_interval_seconds

Per `docs/data-sources.md`, agents should not import provider modules
directly — use the individual domain wrappers (``get_price_history``,
``get_stock_news``, etc.) so the orchestrator can swap real calls for
cached fixtures during tests.

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

from datetime import datetime

from data.timeguard import resolve_as_of

from .models import (
    CompanyRatios,
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
    PriceHistory,
    SocialSentiment,
    SocialSentimentSnapshot,
)
from .rate_limit import AsyncRateLimiter
from .registry import dispatch as _dispatch  # noqa: F401  (re-export)
from .registry import min_decision_interval_seconds


async def get_price_history(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    *,
    as_of: datetime | None = None,
    phase: str | None = None,
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
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)`` (wall
        clock) so live callers that omit this argument behave identically to
        before.  Backtest callers pass the tick timestamp so providers see the
        correct point-in-time.
    phase:
        Tick phase — ``"open"`` or ``"close"``.  Forwarded so cache
        providers can trim the same-day bar at the open tick.  When the
        caller is the live pipeline between scheduled ticks, ``None`` is
        acceptable.

    Returns
    -------
    PriceHistory
        OHLCV bars ordered oldest -> newest.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_price_history")
    return await _dispatch(
        "price_history", ticker.upper(),
        period=period, interval=interval, as_of=as_of, phase=phase,
    )


async def get_company_ratios(
    ticker: str,
    period: str = "1y",
    interval: str = "1d",
    *,
    as_of: datetime | None = None,
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
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.

    Returns
    -------
    CompanyRatios
        Scalar fundamentals + summary stats.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_company_ratios")
    return await _dispatch(
        "company_ratios", ticker.upper(),
        period=period, interval=interval, as_of=as_of,
    )


async def get_stock_news(
    ticker: str,
    from_date=None,
    to_date=None,
    *,
    limit: int | None = 50,
    as_of: datetime | None = None,
):
    """Fetch news articles for ``ticker`` via the active news provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    from_date:
        Start of the news window.  Defaults to ``as_of - 7 days``.
    to_date:
        End of the news window.  Defaults to ``as_of.date()``.
    limit:
        Maximum number of articles to return.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    from datetime import timedelta as _td

    from data.config import get_config
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_stock_news")
    as_of_date = as_of.date()
    # Pull lookback_days from config so the cache provider (which requires
    # the kwarg) and the live Alpha Vantage provider (which honours it when
    # from_date is absent) both receive a consistent value sourced from
    # config/data.json — the single source of truth.
    lookback_days = get_config().defaults.news_lookback_days
    return await _dispatch(
        "news",
        ticker.upper(),
        from_date=from_date or (as_of_date - _td(days=lookback_days)),
        to_date=to_date or as_of_date,
        limit=limit,
        lookback_days=lookback_days,
        as_of=as_of,
    )


async def get_social_sentiment(
    ticker: str,
    *,
    as_of: datetime | None = None,
):
    """Fetch social-sentiment snapshot for ``ticker`` via the active provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_social_sentiment")
    return await _dispatch("social_sentiment", ticker.upper(), as_of=as_of)


async def get_insider_trades(
    ticker: str,
    *,
    lookback_days: int = 30,
    as_of: datetime | None = None,
):
    """Fetch SEC Form 4 insider trades for ``ticker`` via the active provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    lookback_days:
        Lookback window for Form 4 trades.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_insider_trades")
    return await _dispatch(
        "insider_trades", ticker.upper(),
        lookback_days=lookback_days, as_of=as_of,
    )


async def get_public_figure_trades(
    ticker: str | None = None,
    *,
    lookback_days: int = 90,
    as_of: datetime | None = None,
):
    """Fetch politician/congressional trades via the active provider.

    Parameters
    ----------
    ticker:
        Ticker symbol, or ``None`` for all tickers.
    lookback_days:
        Lookback window for trades.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_public_figure_trades")
    return await _dispatch(
        "politician_trades",
        ticker.upper() if ticker else None,
        lookback_days=lookback_days,
        as_of=as_of,
    )


async def get_notable_holders(
    ticker: str,
    *,
    lookback_days: int = 180,
    limit: int = 20,
    as_of: datetime | None = None,
):
    """Fetch notable EDGAR 13F holders for ``ticker`` via the active provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    lookback_days:
        Lookback window for 13F filings.
    limit:
        Maximum number of holders to return.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_notable_holders")
    return await _dispatch(
        "notable_holders", ticker.upper(),
        lookback_days=lookback_days, limit=limit, as_of=as_of,
    )


async def get_company_filings(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    include_excerpts: bool = True,
    as_of: datetime | None = None,
):
    """Fetch SEC filings for ``ticker`` via the active filings provider.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    form_types:
        Tuple of SEC form types to retrieve.
    limit:
        Maximum number of filings per form type.
    include_excerpts:
        Whether to include MD&A / risk-factor excerpts.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``.
    """
    from data.config import get_config
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.get_company_filings")
    # The 8-K staleness horizon for the shared analyst-visibility rule
    # (data.filing_selection) — both the live EDGAR provider and the
    # backtest cache provider consume it, so live and replay selections
    # stay identical.  Sourcing from config keeps single-source-of-truth.
    staleness_days = get_config().defaults.filings_8k_staleness_days
    return await _dispatch(
        "filings", ticker.upper(),
        form_types=form_types, limit=limit,
        include_excerpts=include_excerpts,
        staleness_days=staleness_days,
        as_of=as_of,
    )


__all__ = [
    # Domain wrappers
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
    "SocialSentiment",
    "SocialSentimentSnapshot",
    # Rate limits
    "AsyncRateLimiter",
    "min_decision_interval_seconds",
]
