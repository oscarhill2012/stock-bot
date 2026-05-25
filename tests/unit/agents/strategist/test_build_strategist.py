"""Wiring test for ``agents.strategist.agent.build_strategist``.

Asserts the factory produces a SequentialAgent of
``[StrategistContextShim, RetryingAgentWrapper[LlmAgent], StrategistEnricher]``
with the strategist.llm caps from config/strategist.json threaded through
the wrapper, and the inner LlmAgent's GenerateContentConfig carrying
max_output_tokens=strategist.llm.max_output_tokens.

The third sub-agent — :class:`StrategistEnricher` — was added on
2026-05-25 to lift the narrow-decision → full-decision enrichment out of
the LlmAgent's ``after_agent_callback`` (which broke under schema-retry).
See ``src/agents/strategist/enricher.py`` for the incident analysis.
"""
from __future__ import annotations

from google.adk.agents import SequentialAgent

from agents.llm_retry import RetryingAgentWrapper
from agents.strategist.agent import build_strategist
from agents.strategist.context_shim import StrategistContextShim
from agents.strategist.enricher import StrategistEnricher


def test_build_strategist_wires_llm_caps_from_config() -> None:
    """SequentialAgent[ContextShim, RetryingAgentWrapper[LlmAgent], Enricher]
    with strategist.llm.* threaded through the wrapper."""

    branch = build_strategist()

    assert isinstance(branch, SequentialAgent)
    assert len(branch.sub_agents) == 3

    # Sub-agent ordering is load-bearing: the shim hydrates state the LLM
    # reads via instruction-variable substitution, then the LLM emits the
    # narrow decision, then the enricher derives the full decision dump.
    assert isinstance(branch.sub_agents[0], StrategistContextShim)
    assert isinstance(branch.sub_agents[2], StrategistEnricher)

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
    assert cfg.max_output_tokens == 16000
