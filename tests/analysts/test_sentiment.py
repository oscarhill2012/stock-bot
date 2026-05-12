from google.adk.agents import LlmAgent

from agents.analysts.sentiment.agent import sentiment_analyst


def test_sentiment_analyst_is_llm_agent():
    assert isinstance(sentiment_analyst, LlmAgent)


def test_sentiment_analyst_output_key():
    assert sentiment_analyst.output_key == "sentiment_verdicts"
