"""Technical analyst unit tests (Tier 1 — no LLM)."""
from google.adk.agents import LlmAgent

from agents.analysts.technical.agent import technical_analyst


def test_technical_analyst_is_llm_agent():
    assert isinstance(technical_analyst, LlmAgent)


def test_technical_analyst_name():
    assert technical_analyst.name == "TechnicalAnalyst"


def test_technical_analyst_output_key():
    assert technical_analyst.output_key == "technical_verdicts"
