from google.adk.agents import LlmAgent
from agents.analysts.smart_money.agent import smart_money_analyst


def test_smart_money_analyst_is_llm_agent():
    assert isinstance(smart_money_analyst, LlmAgent)


def test_smart_money_analyst_name():
    assert smart_money_analyst.name == "SmartMoneyAnalyst"


def test_smart_money_analyst_has_no_exhaustive_validator():
    assert smart_money_analyst.after_agent_callback is None
