from google.adk.agents import LlmAgent

from agents.analysts.smart_money.agent import smart_money_analyst


def test_smart_money_analyst_is_llm_agent():
    """SmartMoneyAnalyst is constructed as an ADK LlmAgent singleton."""
    assert isinstance(smart_money_analyst, LlmAgent)


def test_smart_money_analyst_name():
    """The agent identifies itself with the canonical 'SmartMoneyAnalyst' name."""
    assert smart_money_analyst.name == "SmartMoneyAnalyst"


def test_smart_money_analyst_output_key():
    """D3 migrated the output key from signals to verdicts."""
    assert smart_money_analyst.output_key == "smart_money_verdicts"


def test_smart_money_analyst_has_evidence_callback():
    """D3 wires the evidence-only after_agent_callback (replaces dual-emit)."""
    assert smart_money_analyst.after_agent_callback is not None
