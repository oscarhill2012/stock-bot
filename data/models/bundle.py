"""Aggregated payload delivered to the strategist agent."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from .filings import Filing
from .market import StockStats
from .news import NewsArticle
from .sentiment import SocialSentiment
from .trades import InsiderTrade, NotableHolder, PoliticianTrade


class ProviderError(BaseModel):
    """Captured per-provider failure so the bundle can degrade gracefully."""

    provider: str
    message: str


class StockSignalBundle(BaseModel):
    """Single aggregated payload delivered to the strategist agent.

    Any field set to None / [] for a missing provider — see `errors` for
    the captured reason. The agent should treat absent signals as 'no
    information' rather than crashing.

    `min_decision_interval_seconds` is the floor on how often this
    bundle can refresh given the slowest provider's rate budget. The
    strategist must not re-decide faster than this — doing so means
    trading on stale data.
    """

    ticker: str
    generated_at: datetime
    stats: Optional[StockStats] = None
    news: list[NewsArticle] = Field(default_factory=list)
    social_sentiment: Optional[SocialSentiment] = None
    insider_trades: list[InsiderTrade] = Field(default_factory=list)
    politician_trades: list[PoliticianTrade] = Field(default_factory=list)
    notable_holders: list[NotableHolder] = Field(default_factory=list)
    filings: list[Filing] = Field(default_factory=list)
    min_decision_interval_seconds: float = Field(
        default=0.0,
        description=(
            "Floor on the trading cadence implied by the slowest data "
            "source. Decisions made faster than this are made on stale "
            "data."
        ),
    )
    errors: list[ProviderError] = Field(default_factory=list)
