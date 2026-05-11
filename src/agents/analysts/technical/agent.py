"""Technical analyst LlmAgent with dual-emit (legacy signal + new evidence)."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_dual_emit_callback
from contract.extractors.technical import extract_technical_features

from .fetch import technical_fetch_callback
from .prompts import TECHNICAL_INSTRUCTION
from .schema import TechnicalSignal

_after = make_dual_emit_callback(
    analyst="technical",
    signals_key="technical_signals",
    data_key="technical_data",
    evidence_key="technical_evidence",
    extractor=extract_technical_features,
)


technical_analyst = LlmAgent(
    name="TechnicalAnalyst",
    model="gemini-2.5-flash-lite",
    instruction=TECHNICAL_INSTRUCTION,
    output_schema=list[TechnicalSignal],
    output_key="technical_signals",
    before_agent_callback=technical_fetch_callback,
    after_agent_callback=_after,
)


def _build_technical_analyst() -> LlmAgent:
    return LlmAgent(
        name="TechnicalAnalyst",
        model="gemini-2.5-flash-lite",
        instruction=TECHNICAL_INSTRUCTION,
        output_schema=list[TechnicalSignal],
        output_key="technical_signals",
        before_agent_callback=technical_fetch_callback,
        after_agent_callback=_after,
    )
