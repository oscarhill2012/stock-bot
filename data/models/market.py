"""Market-data shapes — output of `get_stock_stats`."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

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
    market_cap: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    beta: Optional[float] = None
    dividend_yield: Optional[float] = None
    fifty_day_average: Optional[float] = None
    two_hundred_day_average: Optional[float] = None
    last_price: Optional[float] = None
    sector: Optional[str] = None
    long_name: Optional[str] = None
