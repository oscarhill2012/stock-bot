"""Technical analyst unit tests (Tier 1 — no LLM).

Phase 5 Task 8: TechnicalAnalyst is a BaseAgent subclass (not LlmAgent).
Plan 09 (audit consolidation): the module-level ``technical_analyst``
singleton was deleted; tests now build a fresh instance via the
``_build_technical_analyst`` factory.
"""
from google.adk.agents import BaseAgent

from agents.analysts.technical.agent import _build_technical_analyst


def test_technical_analyst_is_base_agent():
    """TechnicalAnalyst must be a BaseAgent — it has no LLM dependency."""
    analyst = _build_technical_analyst()
    assert isinstance(analyst, BaseAgent)


def test_technical_analyst_name():
    analyst = _build_technical_analyst()
    assert analyst.name == "TechnicalAnalyst"
