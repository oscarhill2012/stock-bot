"""Fundamental analyst structural tests — A2.5: factory now returns a YieldingAnalystWrapper.

The :func:`build_fundamental_analyst` factory returns a
``YieldingAnalystWrapper`` named ``"FundamentalAnalystBranch"``.  The inner
``LlmAgent`` is accessible via ``.inner`` — tests that need to inspect LlmAgent
attributes do so through that attribute.

Pre-2026-05-21 this file imported a module-level ``fundamental_analyst``
singleton built at import time.  Both that singleton and the hardcoded model
literal have been deleted (the model ID now lives in
``config/models.json::fundamental_analyst`` via
``src.config.models.get_models_config``); these tests now construct a fresh
analyst via :func:`build_fundamental_analyst` for each test module.
"""
from __future__ import annotations

import pytest
from google.adk.agents import LlmAgent

from agents.analysts._base_yield import YieldingAnalystWrapper
from agents.analysts.fundamental.agent import build_fundamental_analyst
from agents.analysts.heuristics import load_heuristics


@pytest.fixture(scope="module")
def fundamental_analyst() -> YieldingAnalystWrapper:
    """Build a fresh ``FundamentalAnalystBranch`` once per test module.

    Loads the closed-vocab heuristics from disk (same call the production
    pipeline makes) and hands the ``fundamental_vocabulary`` to the factory.
    The resulting wrapper is shared across this module's structural tests —
    these tests only inspect identity / type / attributes, so module scope
    is safe.
    """
    h = load_heuristics()
    return build_fundamental_analyst(h.fundamental_vocabulary)


def test_fundamental_analyst_is_yielding_wrapper(fundamental_analyst: YieldingAnalystWrapper) -> None:
    """The factory output must be a YieldingAnalystWrapper (A2.5)."""
    assert isinstance(fundamental_analyst, YieldingAnalystWrapper)


def test_fundamental_analyst_branch_name(fundamental_analyst: YieldingAnalystWrapper) -> None:
    """Outer wrapper name is 'FundamentalAnalystBranch'."""
    assert fundamental_analyst.name == "FundamentalAnalystBranch"


def test_fundamental_analyst_inner_is_llm_agent(fundamental_analyst: YieldingAnalystWrapper) -> None:
    """Inner agent must still be the LlmAgent with correct output_key."""
    assert isinstance(fundamental_analyst.inner, LlmAgent)
    assert fundamental_analyst.inner.output_key == "fundamental_verdicts"


def test_fundamental_analyst_inner_name(fundamental_analyst: YieldingAnalystWrapper) -> None:
    """Inner LlmAgent retains its original 'FundamentalAnalyst' name."""
    assert fundamental_analyst.inner.name == "FundamentalAnalyst"
