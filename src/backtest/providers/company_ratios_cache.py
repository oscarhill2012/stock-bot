"""Company-ratios cache provider — reads scalar fundamentals from the cache store.

Registered as ``("company_ratios", "cache")``.  The live provider
(``data.providers.stats.yfinance``) fetches from Yahoo Finance; this provider
reads the pre-cached snapshot instead, enabling point-in-time-correct backtests
without network calls.

Deviation from the plan's ``stats_cache.py``:
- Phase B retired the ``stats`` domain and split it into ``price_history`` and
  ``company_ratios``.  This provider covers the ``company_ratios`` domain.
- The store method is ``read_company_ratios`` (not ``read_market_meta`` as the
  plan's literal text says — Phase B renamed it).
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import CompanyRatios
from data.registry import register


@register(
    "company_ratios", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    period: str = "1y",
    interval: str = "1d",
    **_unused,
) -> CompanyRatios:
    """Return the latest ``CompanyRatios`` snapshot at or before ``as_of``.

    The cache materialises a daily snapshot, so ``period`` and ``interval``
    are accepted for signature compatibility with the live provider but are
    ignored — backtest analysts that need historical bars query the OHLCV table
    separately via the ``price_history`` domain.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound; only snapshots captured on or before this
        date are considered.
    period, interval:
        Accepted for call-site compatibility; unused.

    Returns
    -------
    CompanyRatios
        The most recent snapshot captured on or before ``as_of``.

    Raises
    ------
    KeyError
        When no snapshot for ``ticker`` exists at or before ``as_of`` in the
        cache store.  Callers should treat this as "no data available" and
        either skip the ticker or use a fallback — consistent with how other
        cache providers signal a missing row.
    """
    result = get_store().read_company_ratios(ticker, as_of=as_of)

    # The store returns None when no snapshot predates as_of.  Raise rather
    # than propagate None, so the return type stays CompanyRatios (not
    # CompanyRatios | None) and matches the canonical DOMAIN_SHAPES entry.
    if result is None:
        raise KeyError(
            f"no company_ratios snapshot for {ticker!r} at or before {as_of}"
        )

    return result
