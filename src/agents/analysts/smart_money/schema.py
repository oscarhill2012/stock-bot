from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field


class SmartMoneySignal(BaseModel):
    ticker: str
    direction: Literal["bullish", "bearish"]  # neutral excluded
    conviction: Literal["low", "high"]
    insiders: list[str] = Field(default_factory=list)
    politicians: list[str] = Field(default_factory=list)
    total_dollar_value: float = 0.0
