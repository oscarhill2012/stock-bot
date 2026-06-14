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


def test_strategist_llm_has_include_contents_none() -> None:
    """The Strategist LlmAgent must be constructed with ``include_contents='none'``.

    ADK's default is ``'default'``, which forwards every upstream agent's
    content events into the strategist's prompt as conversation history — a
    duplication of analyst outputs that the curated ``## Ticker Evidence``
    section already renders.  Setting ``'none'`` suppresses that forwarding
    so the strategist runs purely on its instruction template and the
    ``{temp:*}`` placeholders hydrated by StrategistContextShim.

    This test is a positive sentinel: if someone removes the kwarg or
    changes the value, the duplicate-news regression will re-appear silently.
    """
    branch = build_strategist()

    retrying = branch.sub_agents[1]
    assert isinstance(retrying, RetryingAgentWrapper)

    llm = retrying.inner

    # ``include_contents`` is set on the LlmAgent at construction time.
    # The ADK attribute name mirrors the kwarg name.
    assert getattr(llm, "include_contents", None) == "none", (
        "Strategist LlmAgent must have include_contents='none' to prevent ADK "
        "from forwarding analyst sub-agent outputs as conversation history, "
        "which would duplicate the curated Ticker Evidence block."
    )
