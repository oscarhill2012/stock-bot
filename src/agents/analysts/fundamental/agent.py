"""Fundamental analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.fundamental import extract_fundamental_features

from .fetch import fundamental_fetch_callback
from .prompts import FUNDAMENTAL_INSTRUCTION
from .schema import FundamentalSignal

_after = make_dual_emit_callback(
    analyst="fundamental",
    signals_key="fundamental_signals",
    data_key="fundamental_data",
    evidence_key="fundamental_evidence",
    extractor=extract_fundamental_features,
)


# Module-level singleton used by unit tests that construct the agent directly.
fundamental_analyst = LlmAgent(
    name="FundamentalAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=FUNDAMENTAL_INSTRUCTION,
    output_schema=list[FundamentalSignal],
    output_key="fundamental_signals",
    before_agent_callback=fundamental_fetch_callback,
    after_agent_callback=_after,
)


def _build_fundamental_analyst() -> LlmAgent:
    return LlmAgent(
        name="FundamentalAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=FUNDAMENTAL_INSTRUCTION,
        output_schema=list[FundamentalSignal],
        output_key="fundamental_signals",
        before_agent_callback=fundamental_fetch_callback,
        after_agent_callback=_after,
    )
