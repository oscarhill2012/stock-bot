"""Shared analyst base and callback utilities."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from google.genai import types as genai_types


class AnalystSignal(BaseModel):
    ticker: str
    direction: str  # "bullish" | "bearish" | "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list, max_length=3)
