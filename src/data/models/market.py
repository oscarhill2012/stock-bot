"""Market-data primitives — output of the price-history provider."""
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
