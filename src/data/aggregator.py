"""The agent-facing endpoint.

`get_stock_signal_bundle(ticker)` is the single async function the
strategist agent calls. It fans every provider out concurrently and
returns one `StockSignalBundle` covering price action, fundamentals,
news, social sentiment, insider trades, congressional trades, and
recent filings.

Each provider awaits its own rate-limit token; if a budget is
exhausted the coroutine sleeps until a token frees up — *we wait, we
don't fail*. The bundle's `min_decision_interval_seconds` reports the
slowest source's natural refresh interval so the strategist can keep
its trading cadence above the data-refresh floor.

Partial failures degrade gracefully: a provider that raises has its
exception captured into `bundle.errors` and its slot left as None / [].

Phase 5: ``StockStats`` retired — the ``stats`` domain is split into
``price_history`` and ``company_ratios``. The bundle carries them in
separate typed fields.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import datetime, timedelta
from typing import Any

from . import providers as _providers  # noqa: F401  — triggers @register decorators
from .models import (
    ProviderError,
    StockSignalBundle,
)
from .registry import dispatch, min_decision_interval_seconds
from .timeguard import resolve_as_of

logger = logging.getLogger(__name__)


# Safe defaults used when a provider raises.  Each value must be compatible
# with the corresponding field type on StockSignalBundle.
_DEFAULTS: dict[str, Any] = {
    "price_history": None,
    "company_ratios": None,
    "news": [],
    "social_sentiment": None,
    "insider_trades": [],
    "politician_trades": [],
    "notable_holders": [],
    "filings": [],
}


async def _safe(domain: str, coro: Awaitable, errors: list[ProviderError]) -> Any:
    """Run ``coro`` and return its result; on failure append to ``errors`` and return default.

    Parameters
    ----------
    domain:
        Provider domain name — used to look up the provider name for the error entry.
    coro:
        Awaitable to run.
    errors:
        Mutable list; any captured exception is appended here as a ``ProviderError``.

    Returns
    -------
    Any
        The coroutine's return value, or the domain-specific safe default on failure.
    """
    try:
        return await coro
    except Exception as exc:  # provider boundary — catch-all is intentional
        from .config import get_config

        provider_name = get_config().providers[domain]
        logger.warning("provider %s (%s) failed: %s", provider_name, domain, exc)
        errors.append(ProviderError(
            domain=domain,
            provider=provider_name,
            message=f"{type(exc).__name__}: {exc}",
        ))
        return _DEFAULTS[domain]


async def get_stock_signal_bundle(
    ticker: str,
    *,
    news_lookback_days: int = 7,
    insider_lookback_days: int = 30,
    politician_lookback_days: int = 90,
    notable_holder_lookback_days: int = 180,
    notable_holder_limit: int = 20,
    history_period: str = "1y",
    history_interval: str = "1d",
    filings_per_form: int = 3,
    include_filing_excerpts: bool = True,
    as_of: datetime | None = None,
) -> StockSignalBundle:
    """Aggregate every data-source signal for `ticker` into one payload.

    Every provider runs as its own coroutine; rate limits are enforced
    per-provider via async token buckets. Total wall time is dominated
    by whichever provider's bucket is most depleted.

    Per-provider failures land in `bundle.errors` so the strategist
    can decide how to weigh partial information.

    Parameters
    ----------
    ticker:
        Ticker symbol (will be uppercased).
    news_lookback_days:
        How many days of news articles to fetch.
    insider_lookback_days:
        Lookback window for Form 4 insider trades.
    politician_lookback_days:
        Lookback window for congressional/politician trades.
    notable_holder_lookback_days:
        Lookback window for notable EDGAR 13F holders.
    notable_holder_limit:
        Maximum number of notable holders to return.
    history_period:
        yfinance history period (e.g. ``"1y"``).
    history_interval:
        yfinance history interval (e.g. ``"1d"``).
    filings_per_form:
        Maximum number of filings to retrieve per form type.
    include_filing_excerpts:
        Whether to include MD&A / risk-factor excerpts in filings.
    as_of:
        Historical clock timestamp.  Defaults to ``datetime.now(UTC)``
        (wall clock) so live callers that omit this argument behave
        identically to before.  Backtest callers pass the tick timestamp
        so every downstream provider call sees the correct point-in-time.

    Returns
    -------
    StockSignalBundle
        Aggregated payload. ``bundle.errors`` lists any partial failures.
    """
    symbol = ticker.upper()

    # Resolve the historical clock once; all lookback dates derive from it.
    as_of = resolve_as_of(as_of, allow_wallclock=True, site="data.aggregator")
    as_of_date = as_of.date()

    errors: list[ProviderError] = []

    (
        price_history,
        company_ratios,
        news,
        social,
        insiders,
        politicians,
        holders,
        filings,
    ) = await asyncio.gather(
        _safe(
            "price_history",
            dispatch(
                "price_history", symbol,
                period=history_period, interval=history_interval,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "company_ratios",
            dispatch(
                "company_ratios", symbol,
                period=history_period, interval=history_interval,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "news",
            dispatch(
                "news",
                symbol,
                from_date=as_of_date - timedelta(days=news_lookback_days),
                to_date=as_of_date,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "social_sentiment",
            dispatch("social_sentiment", symbol, as_of=as_of),
            errors,
        ),
        _safe(
            "insider_trades",
            dispatch(
                "insider_trades", symbol,
                lookback_days=insider_lookback_days,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "politician_trades",
            dispatch(
                "politician_trades", symbol,
                lookback_days=politician_lookback_days,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "notable_holders",
            dispatch(
                "notable_holders",
                symbol,
                lookback_days=notable_holder_lookback_days,
                limit=notable_holder_limit,
                as_of=as_of,
            ),
            errors,
        ),
        _safe(
            "filings",
            dispatch(
                "filings",
                symbol,
                limit=filings_per_form,
                include_excerpts=include_filing_excerpts,
                as_of=as_of,
            ),
            errors,
        ),
    )

    return StockSignalBundle(
        ticker=symbol,
        generated_at=as_of,
        price_history=price_history,
        ratios=company_ratios,
        news=news,
        social_sentiment=social,
        insider_trades=insiders,
        politician_trades=politicians,
        notable_holders=holders,
        filings=filings,
        min_decision_interval_seconds=min_decision_interval_seconds(),
        errors=errors,
    )


def get_stock_signal_bundle_blocking(*args, **kwargs) -> StockSignalBundle:
    """Sync wrapper for non-async callers (CLI, tests, ad-hoc scripts).

    Do not call this from inside a running event loop — use the async
    ``get_stock_signal_bundle`` directly there.
    """
    return asyncio.run(get_stock_signal_bundle(*args, **kwargs))
