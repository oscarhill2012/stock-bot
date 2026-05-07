"""Smart-money analyst output schema."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SmartMoneySignal(BaseModel):
    """Signal derived from insider filings, congressional trades, and SC 13D/G holders.

    Only bullish/bearish are emitted — neutral is excluded because the smart-money
    gate skips the LLM entirely when no material activity is detected.
    """

    ticker: str
    direction: Literal["bullish", "bearish"]
    conviction: Literal["low", "high"]
    insiders: list[str] = Field(default_factory=list)       # insider names involved
    politicians: list[str] = Field(default_factory=list)    # politician names involved
    total_dollar_value: float = 0.0                         # USD sum of reported transactions
