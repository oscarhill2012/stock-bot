"""Trade-disclosure shapes — outputs of `get_insider_trades` and `get_public_figure_trades`."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

TradeSide = Literal["buy", "sell", "exchange", "unknown"]


class PoliticianTrade(BaseModel):
    ticker: str
    politician: str
    chamber: Optional[str] = Field(default=None, description="House or Senate.")
    party: Optional[str] = None
    side: TradeSide
    transaction_date: date
    disclosure_date: Optional[date] = None
    amount_min_usd: Optional[float] = None
    amount_max_usd: Optional[float] = None


class InsiderTrade(BaseModel):
    ticker: str
    insider_name: str
    insider_title: Optional[str] = None
    side: TradeSide
    shares: float
    price_per_share: Optional[float] = None
    transaction_date: date
    filed_at: Optional[datetime] = None
    form_type: str = "4"
