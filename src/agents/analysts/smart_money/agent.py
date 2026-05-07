"""Smart-money analyst LlmAgent — interprets insider, politician, and 13D/G signals."""
from __future__ import annotations

from google.adk.agents import LlmAgent

from .fetch import smart_money_fetch_callback
from .prompts import SMART_MONEY_INSTRUCTION
from .schema import SmartMoneySignal

# Module-level singleton used by unit tests that construct the agent directly.
smart_money_analyst = LlmAgent(
    name="SmartMoneyAnalyst",
    model="gemini-2.0-flash-001",
    instruction=SMART_MONEY_INSTRUCTION,
    output_schema=list[SmartMoneySignal],
    output_key="smart_money_signals",
    before_agent_callback=smart_money_fetch_callback,
    # No exhaustive validator — sparse signal by design
)


def _build_smart_money_analyst() -> LlmAgent:
    return LlmAgent(
        name="SmartMoneyAnalyst",
        model="gemini-2.0-flash-001",
        instruction=SMART_MONEY_INSTRUCTION,
        output_schema=list[SmartMoneySignal],
        output_key="smart_money_signals",
        before_agent_callback=smart_money_fetch_callback,
        # No exhaustive validator — sparse signal by design
    )
