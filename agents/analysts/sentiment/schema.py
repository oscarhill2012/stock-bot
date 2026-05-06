from __future__ import annotations
from pydantic import Field
from agents.analysts._common import AnalystSignal


class SentimentSignal(AnalystSignal):
    top_headlines: list[str] = Field(default_factory=list, max_length=2)
    social_score_delta: float = 0.0
