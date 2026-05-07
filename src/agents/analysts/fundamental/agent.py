"""Fundamental analyst LlmAgent — Gemini Flash interprets filings and valuation."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_exhaustive_validator
from .fetch import fundamental_fetch_callback
from .prompts import FUNDAMENTAL_INSTRUCTION
from .schema import FundamentalSignal

# Module-level singleton used by unit tests that construct the agent directly.
fundamental_analyst = LlmAgent(
    name="FundamentalAnalyst",
    model="gemini-2.0-flash-001",
    instruction=FUNDAMENTAL_INSTRUCTION,
    output_schema=list[FundamentalSignal],
    output_key="fundamental_signals",
    before_agent_callback=fundamental_fetch_callback,
    after_agent_callback=make_exhaustive_validator("fundamental_signals"),
)


def _build_fundamental_analyst() -> LlmAgent:
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.0-flash-001",
        instruction=FUNDAMENTAL_INSTRUCTION,
        output_schema=list[FundamentalSignal],
        output_key="fundamental_signals",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=make_exhaustive_validator("fundamental_signals"),
    )
