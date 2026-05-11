"""Strategist output schemas."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from agents.strategist.stance_schema import TickerStance


class PositionThesis(BaseModel):
    """Structured rationale for an open position, created when a position is opened
    and updated on each subsequent tick while the position is held."""

    ticker: str
    opened_at: datetime
    opened_price: float
    opened_tag: str                                    # decision_tag from the opening tick
    rationale: str = Field(max_length=400)             # why we entered
    horizon: Literal["intraday", "swing", "long_term"]
    target_price: float | None = None
    stop_price: float | None   = None
    catalyst: str | None = Field(default=None, max_length=100)
    last_reviewed_at: datetime
    last_review_note: str = Field(default="", max_length=200)
    opened_tick_id: str = ""                           # tick_id that opened this position; populated by the executor on BUY (C13); empty for legacy/pre-tick positions


class StrategistDecision(BaseModel):
    """Output from one Strategist LLM call.

    The LLM emits ``stances`` (per-ticker). The after-callback fills in
    ``target_weights`` / ``new_positions`` / ``close_reasons`` / ``trim_reasons``
    by deriving them from the stances, so downstream consumers see the same
    shape they always saw.
    """

    # Per-ticker stances emitted directly by the LLM; the primary substrate.
    stances: list[TickerStance] = Field(default_factory=list)

    # Weight for every watchlist ticker; must be exhaustive (0 = no position).
    # Derived from stances by the after-callback (C9); defaulted here so that
    # the model can be constructed without them during testing / migration.
    target_weights: dict[str, float] = Field(default_factory=dict)

    decision_tag: str                                  # snake_case label for this tick
    reasoning: str = Field(max_length=300)             # ≤300 char reasoning summary
    updated_thesis: str = Field(max_length=500)        # working hypothesis carried to next tick
    confidence: float = Field(ge=0.0, le=1.0)

    # Required when opening a new position (weight 0 → >0).
    new_positions: dict[str, PositionThesis] = Field(default_factory=dict)
    # Required when closing an existing position (weight >0 → 0).
    close_reasons: dict[str, str] = Field(default_factory=dict)
    # Required when reducing (not zeroing) an existing position.
    trim_reasons: dict[str, str] = Field(default_factory=dict)
