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
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from .models import (
    ProviderError,
    StockSignalBundle,
)
from .providers import (
    get_insider_trades,
    get_public_figure_trades,
)
from .rate_limit import EDGAR, FINNHUB, QUIVER, YFINANCE, slowest_min_interval_seconds
from .registry import dispatch

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, Any] = {
    "stats": None,
    "news": [],
    "social_sentiment": None,
    "insiders": [],
    "politicians": [],
    "notable_holders": [],
    "filings": [],
}


async def _safe(name: str, coro: Awaitable, errors: list[ProviderError]) -> Any:
    try:
        return await coro
    except Exception as exc:  # provider boundary — catch-all is intentional
        logger.warning("provider %s failed: %s", name, exc)
        errors.append(ProviderError(provider=name, message=f"{type(exc).__name__}: {exc}"))
        return _DEFAULTS[name]


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
) -> StockSignalBundle:
    """Aggregate every data-source signal for `ticker` into one payload.

    Every provider runs as its own coroutine; rate limits are enforced
    per-provider via async token buckets. Total wall time is dominated
    by whichever provider's bucket is most depleted.

    Per-provider failures land in `bundle.errors` so the strategist
    can decide how to weigh partial information.
    """
    symbol = ticker.upper()
    today = date.today()
    errors: list[ProviderError] = []

    stats, news, social, insiders, politicians, holders, filings = await asyncio.gather(
        _safe("stats", dispatch("stats", symbol, period=history_period, interval=history_interval), errors),
        _safe(
            "news",
            dispatch("news", symbol,
                     from_date=today - timedelta(days=news_lookback_days),
                     to_date=today),
            errors,
        ),
        _safe("social_sentiment", dispatch("social_sentiment", symbol), errors),
        _safe("insiders", get_insider_trades(symbol, lookback_days=insider_lookback_days), errors),
        _safe(
            "politicians",
            get_public_figure_trades(symbol, lookback_days=politician_lookback_days),
            errors,
        ),
        _safe(
            "notable_holders",
            dispatch("notable_holders", symbol,
                     lookback_days=notable_holder_lookback_days,
                     limit=notable_holder_limit),
            errors,
        ),
        _safe(
            "filings",
            dispatch("filings", symbol,
                     limit=filings_per_form,
                     include_excerpts=include_filing_excerpts),
            errors,
        ),
    )

    return StockSignalBundle(
        ticker=symbol,
        generated_at=datetime.now(tz=UTC),
        stats=stats,
        news=news,
        social_sentiment=social,
        insider_trades=insiders,
        politician_trades=politicians,
        notable_holders=holders,
        filings=filings,
        min_decision_interval_seconds=slowest_min_interval_seconds(
            FINNHUB, QUIVER, EDGAR, YFINANCE
        ),
        errors=errors,
    )


def get_stock_signal_bundle_blocking(*args, **kwargs) -> StockSignalBundle:
    """Sync wrapper for non-async callers (CLI, tests, ad-hoc scripts).

    Do not call this from inside a running event loop — use the async
    `get_stock_signal_bundle` directly there.
    """
    return asyncio.run(get_stock_signal_bundle(*args, **kwargs))
