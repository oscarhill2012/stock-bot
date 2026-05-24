"""Wiring test for ``agents.strategist.agent.build_strategist``.

Asserts the factory produces a SequentialAgent whose second sub-agent
is a RetryingAgentWrapper carrying the strategist.llm caps from
config/strategist.json, and whose inner LlmAgent receives a
GenerateContentConfig with max_output_tokens=strategist.llm.max_output_tokens.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.llm_retry import RetryingAgentWrapper
from agents.strategist.agent import build_strategist


def test_build_strategist_wires_llm_caps_from_config() -> None:
    """SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent]] with
    strategist.llm.* threaded through."""

    branch = build_strategist()

    assert isinstance(branch, SequentialAgent)
    assert len(branch.sub_agents) == 2

    retrying = branch.sub_agents[1]
    assert isinstance(retrying, RetryingAgentWrapper)

    assert retrying.timeout_seconds == 180
    assert retrying.retry_state_key == "temp:_obs_strategist_retries"
    assert set(retrying.policies.keys()) == {"rate_limit", "timeout", "schema"}
    assert retrying.policies["timeout"].max_attempts == 3
    assert retrying.policies["schema"].max_attempts  == 3

    llm = retrying.inner
    cfg = llm.generate_content_config

    assert cfg is not None
    assert cfg.max_output_tokens == 8000
