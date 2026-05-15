"""Insider-trades cache provider — reads Form 4 disclosures from the cache store.

Registered as ``("insider_trades", "cache")``.  The live provider fetches from
EDGAR; this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``filed_at`` (Form 4 filing date), never on
``transaction_date``.  Trades can be transacted days before their SEC filing;
using ``transaction_date`` as the filter would expose future-filed data to a
backtest running at ``as_of``, introducing lookahead bias.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import InsiderTrade
from data.registry import register


@register(
    "insider_trades", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int = 90,
    **_unused,
) -> list[InsiderTrade]:
    """Return insider trades filed at or before ``as_of``.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``filed_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include (default 90).

    Returns
    -------
    list[InsiderTrade]
        Matching trades, most-recently-filed first.
    """
    return get_store().read_insider_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )
