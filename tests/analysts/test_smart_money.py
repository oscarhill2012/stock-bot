from google.adk.agents import LlmAgent

from agents.analysts.smart_money.agent import smart_money_analyst


def test_smart_money_analyst_is_llm_agent():
    """SmartMoneyAnalyst is constructed as an ADK LlmAgent singleton."""
    assert isinstance(smart_money_analyst, LlmAgent)


def test_smart_money_analyst_name():
    """The agent identifies itself with the canonical 'SmartMoneyAnalyst' name."""
    assert smart_money_analyst.name == "SmartMoneyAnalyst"


def test_smart_money_analyst_has_dual_emit_callback():
    """Plan B wires a dual-emit after_agent_callback (legacy signal + evidence).

    Prior behaviour was an absent after_agent_callback because the analyst was
    treated as sparse-by-design. Plan B keeps the sparse short-circuit on the
    fetch side but adds a dual-emit aggregator afterwards (with ``sparse=True``
    so the exhaustive re-prompt is disabled).
    """
    assert smart_money_analyst.after_agent_callback is not None
