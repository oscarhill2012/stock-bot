"""Trade-disclosure shapes — outputs of `get_insider_trades` and `get_public_figure_trades`."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

TradeSide = Literal["buy", "sell", "exchange", "unknown"]


class PoliticianTrade(BaseModel):
    ticker: str
    politician: str
    chamber: str | None = Field(default=None, description="House or Senate.")
    party: str | None = None
    side: TradeSide
    # Accepts both date and datetime so the cache layer can receive
    # fully-timestamped values after the Date → DateTime schema migration.
    transaction_date: date | datetime
    disclosure_date: date | datetime | None = None
    amount_min_usd: float | None = None
    amount_max_usd: float | None = None


class InsiderTrade(BaseModel):
    """One Form 4 common-stock transaction row.

    Captures both the structured fields the deterministic extractor consumes
    and the narrative supplement (footnote + transaction code + 10b5-1 flag)
    that lets the Fundamental LLM separate mechanical sales from
    discretionary ones.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide
    shares: float
    price_per_share: float | None = None
    transaction_date: date
    filed_at: datetime
    form_type: str

    # Narrative + categorical supplement added in Phase 5.
    transaction_code: str | None = None   # P/S/A/M/F/G/D/X — Form 4 Table I col 3
    is_10b5_1: bool = False               # From the form-level flag or footnote regex
    footnote: str | None = None           # Free-text footnote on the row (prose)


class InsiderDerivativeTrade(BaseModel):
    """One Form 4 derivative-securities transaction row.

    Option exercises, option grants, RSU vestings, warrant transactions.
    Strike + underlying-shares + footnote together describe whether a
    transaction is dilutive vesting, an in-the-money exercise, an
    exercise-and-hold (bullish), or an exercise-and-dump.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ticker: str
    insider_name: str
    insider_title: str | None = None
    side: TradeSide
    derivative_type: str | None = None    # "option", "rsu", "warrant", "performance_award"
    underlying_shares: float
    strike_price: float | None = None
    transaction_date: date
    filed_at: datetime
    transaction_code: str | None = None
    is_10b5_1: bool = False
    footnote: str | None = None


class Form4Bundle(BaseModel):
    """One ticker's parsed Form 4 contents — both transaction tables.

    `trades` carries the common-stock rows (Table I of the form).
    `derivatives` carries the derivative-securities rows (Table II).
    Both lists may be empty if the form contained no transactions in
    that table (e.g. a purely derivative exercise or a grants-only form).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    trades: list[InsiderTrade] = Field(default_factory=list)
    derivatives: list[InsiderDerivativeTrade] = Field(default_factory=list)


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
    url: str | None = None
