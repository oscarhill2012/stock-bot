"""Market-data shapes — output of `get_stock_stats`."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class OHLCBar(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class StockStats(BaseModel):
    ticker: str
    history: list[OHLCBar]
    market_cap: float | None = None
    trailing_pe: float | None = None
    forward_pe: float | None = None
    beta: float | None = None
    dividend_yield: float | None = None
    fifty_day_average: float | None = None
    two_hundred_day_average: float | None = None
    last_price: float | None = None
    sector: str | None = None
    long_name: str | None = None
