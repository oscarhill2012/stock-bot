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


class NotableHolder(BaseModel):
    """A 5%+ beneficial-ownership disclosure (SC 13D / 13G + amendments).

    These filings are the closest free SEC analog to Quiver-style
    "smart money" tracking — activist funds, large index holders, and
    notable investors must file when crossing the 5% threshold or
    materially changing their stake.

    `intent` distinguishes 13D (active / activist) from 13G (passive).
    `is_amendment` flags amendments (13D/A, 13G/A) which usually signal
    a stake change rather than a fresh position.
    """

    ticker: str
    holder: str
    form_type: str  # "SC 13D", "SC 13G", "SC 13D/A", "SC 13G/A"
    intent: Literal["active", "passive", "unknown"] = "unknown"
    is_amendment: bool = False
    filed_at: datetime
    accession_no: str
    url: Optional[str] = None
