"""Insider-trades cache provider — reads Form 4 disclosures from the cache store.

Registered as ``("insider_trades", "cache")``.  The live provider fetches from
EDGAR; this provider reads the pre-cached rows instead.

PIT filter: the store filters on ``filed_at`` (Form 4 filing date), never on
``transaction_date``.  Trades can be transacted days before their SEC filing;
using ``transaction_date`` as the filter would expose future-filed data to a
backtest running at ``as_of``, introducing lookahead bias.

Shape note: the cache store persists only common-stock rows (Table I of Form 4);
derivative-securities rows (Table II) are not cached.  The live EDGAR provider
returns a ``Form4Bundle`` containing both tables.  To match live pipeline parity
this provider wraps the flat row list in ``Form4Bundle(trades=...,
derivatives=[])``, so ``fundamental/fetch.py``'s ``isinstance(bundle,
Form4Bundle)`` guard passes instead of silently degrading to an empty bundle.
"""
from __future__ import annotations

from datetime import datetime

from backtest.providers._store_handle import get_store
from data.models import Form4Bundle
from data.registry import register


@register(
    "insider_trades", "cache",
    upstream="cache", rate_per_minute=1_000_000, burst=1_000,
)
async def fetch(
    ticker: str,
    *,
    as_of: datetime,
    lookback_days: int,   # required — defaults now flow from get_config() in caller
    **_unused,
) -> Form4Bundle:
    """Return insider trades filed at or before ``as_of``, wrapped in a Form4Bundle.

    The cache store returns a flat ``list[InsiderTrade]`` (common-stock rows
    only — derivative rows are not persisted to the golden cache).  This
    provider re-shapes that list into a ``Form4Bundle`` so it matches the shape
    the live EDGAR provider returns, allowing ``fundamental/fetch.py`` to pass
    the bundle through its ``isinstance(bundle, Form4Bundle)`` guard without
    silently falling back to an empty bundle.

    Parameters
    ----------
    ticker:
        Ticker symbol (e.g. ``"AAPL"``).
    as_of:
        Point-in-time upper bound (inclusive) on ``filed_at``.
    lookback_days:
        How many calendar days before ``as_of`` to include.  Required — the
        caller is responsible for supplying the value from ``get_config()``.

    Returns
    -------
    Form4Bundle
        Bundle with ``trades`` populated from cache rows and ``derivatives``
        set to an empty list (derivative rows are not cached).
    """
    trades = get_store().read_insider_trades(
        ticker, as_of=as_of, lookback_days=lookback_days,
    )

    # Wrap the flat list in Form4Bundle to match the live provider's return
    # shape.  The live EDGAR provider returns a bundle with both tables; the
    # cache only stores Table I (common-stock rows), so derivatives=[].
    return Form4Bundle(trades=trades, derivatives=[])
