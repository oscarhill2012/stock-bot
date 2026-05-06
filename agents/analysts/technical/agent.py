"""Technical analyst LlmAgent."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._common import make_exhaustive_validator
from .fetch import technical_fetch_callback
from .prompts import TECHNICAL_INSTRUCTION
from .schema import TechnicalSignal

technical_analyst = LlmAgent(
    name="TechnicalAnalyst",
    model="gemini-2.0-flash-001",
    instruction=TECHNICAL_INSTRUCTION,
    output_schema=list[TechnicalSignal],
    output_key="technical_signals",
    before_agent_callback=technical_fetch_callback,
    after_agent_callback=make_exhaustive_validator("technical_signals"),
)
