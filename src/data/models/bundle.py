"""Aggregated payload delivered to the strategist agent."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from .analyst_consensus import AnalystRating, AnalystRevision
from .company_ratios import CompanyRatios
from .earnings import EarningsHistory, EarningsReport  # noqa: F401 — re-exported
from .filings import Filing
from .news import NewsArticle
from .price_history import PriceHistory
from .sentiment import SocialSentiment
from .short_interest import ShortInterestSnapshot
from .trades import InsiderTrade, NotableHolder, PoliticianTrade


class ProviderError(BaseModel):
    """Captured per-provider failure so the bundle can degrade gracefully."""

    domain: str
    provider: str
    message: str


class StockSignalBundle(BaseModel):
    """Single aggregated payload delivered to the strategist agent.

    Any field set to None / [] for a missing provider — see ``errors`` for
    the captured reason. The agent should treat absent signals as 'no
    information' rather than crashing.

    ``min_decision_interval_seconds`` is the floor on how often this
    bundle can refresh given the slowest provider's rate budget. The
    strategist must not re-decide faster than this — doing so means
    trading on stale data.

    Phase 5 data-model split: ``StockStats`` is retired. Price history and
    scalar fundamentals now live in separate typed fields (``price_history``
    and ``ratios``) so consumers can request only what they need.
    """

    ticker: str
    generated_at: datetime

    # Phase 5: split from the retired StockStats — Technical uses price_history;
    # Fundamental uses ratios only.
    price_history: PriceHistory | None = None
    ratios: CompanyRatios | None = None

    news: list[NewsArticle] = Field(default_factory=list)
    social_sentiment: SocialSentiment | None = None
    insider_trades: list[InsiderTrade] = Field(default_factory=list)
    politician_trades: list[PoliticianTrade] = Field(default_factory=list)
    notable_holders: list[NotableHolder] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)

    # --- Phase 7 extensions — new data domains ---
    # All default to None / [] so existing cached trace data round-trips
    # without modification (additive-only change).
    earnings: list[EarningsReport] = Field(
        default_factory=list,
        description="Recent quarterly earnings reports from the Finnhub provider.",
    )
    analyst_consensus: AnalystRating | None = Field(
        default=None,
        description="Latest analyst consensus price-target + recommendation snapshot.",
    )
    analyst_revisions: list[AnalystRevision] = Field(
        default_factory=list,
        description="Recent upgrade / downgrade / target-change events.",
    )
    short_interest: ShortInterestSnapshot | None = Field(
        default=None,
        description=(
            "Most recent short-interest snapshot (or v1 synthesis proxy). "
            "None when the FINRA provider is absent or returned no data."
        ),
    )

    min_decision_interval_seconds: float = Field(
        default=0.0,
        description=(
            "Floor on the trading cadence implied by the slowest data "
            "source. Decisions made faster than this are made on stale "
            "data."
        ),
    )
    errors: list[ProviderError] = Field(default_factory=list)
