"""Technical analyst unit tests (Tier 1 — no LLM).

Updated for Phase 5 Task 8: TechnicalAnalyst is now a BaseAgent subclass
(not an LlmAgent).  The output_key field belongs to LlmAgent and no longer
applies; verdicts are written directly to state["technical_verdicts"] by
_run_async_impl.
"""
from google.adk.agents import BaseAgent

from agents.analysts.technical.agent import technical_analyst


def test_technical_analyst_is_base_agent():
    """TechnicalAnalyst must be a BaseAgent — it has no LLM dependency."""
    assert isinstance(technical_analyst, BaseAgent)


def test_technical_analyst_name():
    assert technical_analyst.name == "TechnicalAnalyst"
