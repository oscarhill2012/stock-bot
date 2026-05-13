"""Smoke-tests for the SmartMoneyAnalyst module-level singleton.

These tests verify the Phase-5 BaseAgent shape: the analyst is no longer
an LlmAgent — it is a deterministic BaseAgent subclass wired with
heuristics-driven verdict logic.
"""
from google.adk.agents import BaseAgent

from agents.analysts.smart_money.agent import SmartMoneyAnalyst, smart_money_analyst


def test_smart_money_analyst_is_base_agent():
    """SmartMoneyAnalyst is a BaseAgent subclass (LlmAgent retired in Phase 5)."""
    assert isinstance(smart_money_analyst, BaseAgent)


def test_smart_money_analyst_is_smart_money_analyst_instance():
    """The singleton is an instance of the concrete SmartMoneyAnalyst class."""
    assert isinstance(smart_money_analyst, SmartMoneyAnalyst)


def test_smart_money_analyst_name():
    """The agent identifies itself with the canonical 'SmartMoneyAnalyst' name."""
    assert smart_money_analyst.name == "SmartMoneyAnalyst"


def test_smart_money_analyst_has_evidence_callback():
    """Phase 5 wires the evidence-only after_agent_callback."""
    assert smart_money_analyst.after_agent_callback is not None


def test_smart_money_analyst_has_heuristics():
    """The singleton carries a SmartMoneyHeuristics instance as a Pydantic field."""
    from agents.analysts.heuristics import SmartMoneyHeuristics

    assert isinstance(smart_money_analyst.heuristics, SmartMoneyHeuristics)
