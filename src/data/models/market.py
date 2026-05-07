"""Market-data shapes — output of `get_stock_stats`."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OHLCBar(BaseModel):
    """One price bar from yfinance history (OHLCV adjusted for splits and dividends)."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class StockStats(BaseModel):
    """Aggregated market data for one ticker returned by the yfinance provider.

    All fundamental fields are Optional — yfinance sometimes returns None or
    non-finite values, which the provider normalises to None.
    """

    ticker: str
    history: list[OHLCBar]          # daily OHLCV bars for the requested period

    # Fundamentals — may be None for tickers with incomplete yfinance coverage.
    market_cap: float | None              = None
    trailing_pe: float | None             = None
    forward_pe: float | None              = None
    beta: float | None                    = None
    dividend_yield: float | None          = None
    fifty_day_average: float | None       = None
    two_hundred_day_average: float | None = None
    last_price: float | None              = None  # most recent trade price
    sector: str | None                    = None
    long_name: str | None                 = None
