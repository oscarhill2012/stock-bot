"""Sentiment analyst LlmAgent — Gemini Flash scores news and social signals."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_exhaustive_validator
from .fetch import sentiment_fetch_callback
from .prompts import SENTIMENT_INSTRUCTION
from .schema import SentimentSignal

# Module-level singleton used by unit tests that construct the agent directly.
sentiment_analyst = LlmAgent(
    name="SentimentAnalyst",
    model="gemini-2.0-flash-001",
    instruction=SENTIMENT_INSTRUCTION,
    output_schema=list[SentimentSignal],
    output_key="sentiment_signals",
    before_agent_callback=sentiment_fetch_callback,
    after_agent_callback=make_exhaustive_validator("sentiment_signals"),
)


def _build_sentiment_analyst() -> LlmAgent:
    return LlmAgent(
        name="SentimentAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SENTIMENT_INSTRUCTION,
        output_schema=list[SentimentSignal],
        output_key="sentiment_signals",
        before_agent_callback=sentiment_fetch_callback,
        after_agent_callback=make_exhaustive_validator("sentiment_signals"),
    )
