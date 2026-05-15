"""Price-history cache provider — reads OHLCV bars from the cache store.

Registered as ``("price_history", "cache")``.  The live provider
(``data.providers.stats.yfinance``) fetches from Yahoo Finance; this provider
reads the pre-cached OHLCV bars instead, enabling point-in-time-correct
backtests without network calls.

Deviation from the plan's ``stats_cache.py``:
- Phase B retired the ``stats`` domain and split it into ``price_history`` and
  ``company_ratios``.  This provider covers the ``price_history`` domain.
- The store's ``read_ohlcv`` takes ``(ticker, start, end)`` as ``date`` objects;
  this provider converts ``as_of`` + a lookback (derived from ``period``) into
  that date range.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from backtest.providers._store_handle import get_store
from data.models import PriceHistory
from data.registry import register

# Map yfinance period strings to approximate calendar-day lookbacks.
_PERIOD_DAYS: dict[str, int] = {
    "1d":  1,
    "5d":  5,
    "1mo": 31,
    "3mo": 92,
    "6mo": 183,
    "1y":  365,
    "2y":  730,
    "5y":  1825,
    "10y": 3650,
    "ytd": 365,    # conservative approximation
    "max": 36500,  # ~100 years; effectively "all available"
}


@register(
    "price_history", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused,
) -> PriceHistory:
    """Return OHLCV bars for ``ticker`` up to and including ``as_of``.

    ``period`` is converted to an approximate calendar-day lookback so the
    query matches the window the live provider would return.  ``interval`` is
    accepted for signature compatibility but ignored — the cache stores daily
    bars exclusively.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound; bars after this date are excluded.
    period:
        yfinance-style period string (e.g. ``"1y"``).  Unknown strings fall
        back to 365 days.
    interval:
        Accepted for call-site compatibility; unused.

    Returns
    -------
    PriceHistory
        Bars in ascending date order.  Empty list when no cached bars exist.
    """
    lookback_days = _PERIOD_DAYS.get(period, 365)
    end: date   = as_of.date()
    start: date = end - timedelta(days=lookback_days)

    bars = get_store().read_ohlcv(ticker, start=start, end=end)

    return PriceHistory(ticker=ticker, bars=bars)
