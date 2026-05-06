"""Shared analyst base and callback utilities."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from google.genai import types as genai_types
from google.adk.agents.callback_context import CallbackContext


class AnalystSignal(BaseModel):
    ticker: str
    direction: str  # "bullish" | "bearish" | "neutral"
    confidence: float = Field(ge=0.0, le=1.0)
    key_factors: list[str] = Field(default_factory=list, max_length=3)


def make_exhaustive_validator(
    signals_key: str,
    tickers_key: str = "tickers",
):
    """Return an after_agent_callback that re-prompts if any watchlist tickers are missing."""

    def _validator(callback_context: CallbackContext) -> Optional[genai_types.Content]:
        state = callback_context.state
        signals = state.get(signals_key, [])
        tickers = state.get(tickers_key, [])
        if not tickers:
            return None
        emitted = {
            (s["ticker"] if isinstance(s, dict) else s.ticker)
            for s in signals
        }
        missing = [t for t in tickers if t not in emitted]
        if missing:
            return genai_types.Content(
                parts=[genai_types.Part(
                    text=f"You missed these tickers: {missing}. "
                         f"Please emit a signal for every watchlist ticker."
                )],
                role="user",
            )
        return None

    return _validator
