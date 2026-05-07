"""Sentiment analyst output schema — extends AnalystSignal with news/social fields."""
from __future__ import annotations

from pydantic import Field

from agents.analysts._common import AnalystSignal


class SentimentSignal(AnalystSignal):
    """News + social sentiment signal for one ticker."""

    # Up to 2 headline strings that drove the direction call.
    top_headlines: list[str] = Field(default_factory=list, max_length=2)
    # Positive = sentiment improving vs recent baseline; negative = deteriorating.
    social_score_delta: float = 0.0
