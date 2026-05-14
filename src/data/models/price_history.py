"""``PriceHistory`` — OHLCV bars for one ticker, ordered oldest -> newest."""
from __future__ import annotations

from pydantic import BaseModel

from .market import OHLCBar


class PriceHistory(BaseModel):
    """Daily OHLCV bars for one ticker, ordered oldest -> newest.

    Replaces the ``history`` field of the retired ``StockStats`` model. The
    Technical analyst is the only consumer.

    Parameters
    ----------
    ticker:
        Upper-cased symbol the bars belong to.
    bars:
        List of ``OHLCBar`` records. May be empty for tickers the provider
        has no coverage of.
    """

    ticker: str
    bars: list[OHLCBar]
