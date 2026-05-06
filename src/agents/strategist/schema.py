"""Strategist output schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PositionThesis(BaseModel):
    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str
    rationale: str = Field(max_length=400)
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None = None
    catalyst: str | None = Field(default=None, max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=200)


class StrategistDecision(BaseModel):
    target_weights: dict[str, float]
    decision_tag: str
    reasoning: str = Field(max_length=300)
    updated_thesis: str = Field(max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    close_reasons: dict[str, str] = Field(default_factory=dict)
