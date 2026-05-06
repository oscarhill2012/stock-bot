"""Social-sentiment shapes — output of `get_social_sentiment`."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SocialSentimentSnapshot(BaseModel):
    platform: Literal["reddit", "twitter", "other"]
    mention_count: int = 0
    positive_score: float = 0.0
    negative_score: float = 0.0
    score: float = Field(
        default=0.0,
        description="Net sentiment in [-1.0, 1.0] (positive - negative, normalised).",
    )


class SocialSentiment(BaseModel):
    ticker: str
    snapshots: list[SocialSentimentSnapshot]
    aggregate_score: float = Field(
        default=0.0,
        description="Mention-weighted net sentiment across all platforms.",
    )
