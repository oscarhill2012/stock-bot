"""Sentiment analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.sentiment import extract_sentiment_features

from .fetch import sentiment_fetch_callback
from .prompts import SENTIMENT_INSTRUCTION
from .schema import SentimentSignal

_after = make_dual_emit_callback(
    analyst="sentiment",
    signals_key="sentiment_signals",
    data_key="sentiment_data",
    evidence_key="sentiment_evidence",
    extractor=extract_sentiment_features,
)


# Module-level singleton used by unit tests that construct the agent directly.
sentiment_analyst = LlmAgent(
    name="SentimentAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=SENTIMENT_INSTRUCTION,
    output_schema=list[SentimentSignal],
    output_key="sentiment_signals",
    before_agent_callback=sentiment_fetch_callback,
    after_agent_callback=_after,
)


def _build_sentiment_analyst() -> LlmAgent:
    return LlmAgent(
        name="SentimentAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=SENTIMENT_INSTRUCTION,
        output_schema=list[SentimentSignal],
        output_key="sentiment_signals",
        before_agent_callback=sentiment_fetch_callback,
        after_agent_callback=_after,
    )
