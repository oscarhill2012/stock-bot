from google.adk.agents import LlmAgent

from agents.analysts.fundamental.agent import fundamental_analyst


def test_fundamental_analyst_is_llm_agent():
    assert isinstance(fundamental_analyst, LlmAgent)


def test_fundamental_analyst_name():
    assert fundamental_analyst.name == "FundamentalAnalyst"


def test_fundamental_analyst_output_key():
    assert fundamental_analyst.output_key == "fundamental_verdicts"
