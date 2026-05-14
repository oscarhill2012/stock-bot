"""Stats (market-meta) cache provider — reads from ``CachedDataStore``.

Registered as ``upstream="cache"`` so the backtest runner can point the
``stats`` domain at this provider by calling
``set_active_provider("stats", "cache")``.

The ``period`` / ``interval`` kwargs accepted by the live yfinance provider
are absorbed and ignored; the cache derives the equivalent lookback from the
``OHLCV_LOOKBACK_DAYS`` constant and populates ``StockStats.history`` with
the appropriate bars so technical indicators have price data available.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from backtest.providers._store_handle import get_store
from data.models import StockStats
from data.registry import register

# How many calendar days of OHLCV history to attach to each StockStats.
# The live yfinance provider uses ``period="1y"`` by default, which covers
# roughly 252 trading days.  365 calendar days is a safe upper bound that
# ensures RSI(14), ATR(14), 200-day SMA, and all other technical indicators
# have enough bars even after accounting for weekends/holidays.
_OHLCV_LOOKBACK_DAYS = 365


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
    """Return the latest market-meta snapshot at or before ``as_of``, with
    OHLCV ``history`` populated from the cache.

    The live yfinance provider returns a ``StockStats`` whose ``history``
    field contains up to one year of daily OHLCV bars.  This cache provider
    replicates that contract by reading bars from the OHLCV table using a
    ``_OHLCV_LOOKBACK_DAYS``-day window ending at ``as_of``.  Without this,
    every technical indicator in the extractor evaluates to zero.

    Parameters
    ----------
    ticker:
        The equity symbol to query.
    as_of:
        Point-in-time ceiling — the most recent snapshot whose ``as_of_date``
        is ``<= as_of.date()`` is returned, and OHLCV bars are filtered to
        ``<= as_of`` (no lookahead bias).
    period:
        Accepted for live-provider signature compatibility; ignored by cache.
    interval:
        Accepted for live-provider signature compatibility; ignored by cache.
    **_unused:
        Absorbs any other live-provider kwargs.

    Returns
    -------
    StockStats | None
        The most recent snapshot with ``history`` populated from the OHLCV
        cache, or ``None`` if no cached market-meta data exists for the ticker
        before ``as_of``.
    """
    store = get_store()

    stats = store.read_market_meta(ticker, as_of=as_of)
    if stats is None:
        return None

    # Populate ``history`` with OHLCV bars so technical extractors have
    # price data — the store's ``read_ohlcv`` already enforces the
    # point-in-time filter (bars after ``as_of`` are excluded).
    lookback_start = (as_of - timedelta(days=_OHLCV_LOOKBACK_DAYS)).date()
    bars = store.read_ohlcv(ticker, start=lookback_start, end=as_of.date())

    # Return a new StockStats with the populated history; all other fields
    # are copied unchanged from the snapshot read by read_market_meta.
    return stats.model_copy(update={"history": bars})
