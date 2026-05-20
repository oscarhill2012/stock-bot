"""Fundamental analyst structural tests — A2.5: factory now returns a YieldingAnalystWrapper.

The module-level singleton ``fundamental_analyst`` is a
``YieldingAnalystWrapper`` named ``"FundamentalAnalystBranch"``.  The inner
``LlmAgent`` is accessible via ``.inner`` — tests that need to inspect LlmAgent
attributes do so through that attribute.
"""
from __future__ import annotations

from google.adk.agents import LlmAgent

from agents.analysts._base_yield import YieldingAnalystWrapper
from agents.analysts.fundamental.agent import fundamental_analyst


def test_fundamental_analyst_is_yielding_wrapper() -> None:
    """The module singleton must now be a YieldingAnalystWrapper (A2.5)."""
    assert isinstance(fundamental_analyst, YieldingAnalystWrapper)


def test_fundamental_analyst_branch_name() -> None:
    """Outer wrapper name is 'FundamentalAnalystBranch'."""
    assert fundamental_analyst.name == "FundamentalAnalystBranch"


def test_fundamental_analyst_inner_is_llm_agent() -> None:
    """Inner agent must still be the LlmAgent with correct output_key."""
    assert isinstance(fundamental_analyst.inner, LlmAgent)
    assert fundamental_analyst.inner.output_key == "fundamental_verdicts"


def test_fundamental_analyst_inner_name() -> None:
    """Inner LlmAgent retains its original 'FundamentalAnalyst' name."""
    assert fundamental_analyst.inner.name == "FundamentalAnalyst"
