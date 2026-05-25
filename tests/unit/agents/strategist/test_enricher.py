"""StrategistEnricher BaseAgent tests — Tier 1, no LLM.

The enricher is a dedicated BaseAgent that runs after the wrapped LlmAgent
in the StrategistBranch SequentialAgent.  Its job is to read the narrow
``StrategistLLMDecision`` (emitted by the LLM via ``output_key``) from
session state, run the contract-validation passes + ``derive_decision_fields``,
then yield a single ``Event`` with ``state_delta`` containing the enriched
full ``StrategistDecision`` dump.

This lifts the enrichment out of the LlmAgent's ``after_agent_callback``,
which was lifecycle-coupled to the LLM call and silently misfired under
schema-retry.  Sequencing it as a separate BaseAgent makes the enrichment
run unconditionally after the wrapper succeeds — regardless of how many
retries were needed inside the wrapped LLM agent.

See: regression for the 2026-05-24 schema-retry-eats-enrichment bug.
"""
from __future__ import annotations

import asyncio

import pytest

from agents.strategist.derivation import StrategistContractViolation
from agents.strategist.enricher import StrategistEnricher
from agents.strategist.schema import StrategistLLMDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio


# ---------------------------------------------------------------------------
# Minimal stub for ADK InvocationContext — only exposes session.state.
# Mirrors the pattern in test_decision_writer.py so the two BaseAgent test
# files stay visually consistent.
# ---------------------------------------------------------------------------

class _StubCtx:
    """Minimal stand-in for ADK InvocationContext.

    Exposes ``session.state`` and ``invocation_id`` — the latter is required
    because the enricher yields an ``Event`` whose ``invocation_id`` field
    is Pydantic-validated.
    """

    def __init__(self, state: dict):
        class _S:
            pass
        self.session = _S()
        self.session.state = state
        self.invocation_id = "test-invocation"


def _run(coro_gen):
    """Drain an async generator to a list synchronously."""
    async def _drain():
        return [ev async for ev in coro_gen]
    return asyncio.run(_drain())


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _narrow_llm_output(tickers: list[str]) -> dict:
    """Build the *narrow* StrategistLLMDecision dump the LLM emits via output_key.

    Crucially this dict has NO ``target_weights`` / ``close_reasons`` /
    ``trim_reasons`` keys — those are derived downstream by the enricher.
    A fresh ``StrategistLLMDecision`` matches exactly what arrives at the
    enricher after the LlmAgent's ``output_key`` write lands in state.
    """
    return StrategistLLMDecision(
        stances=[
            TickerStance(
                ticker       = tickers[0],
                intent       = "open",
                weight       = 0.05,
                rationale    = "open justification text",
                horizon      = "swing",
                target_price = 210.0,
                stop_price   = 185.0,
            ),
        ],
        decision_tag = "open_first",
        reasoning    = "Cold-start open on first watchlist ticker.",
        thesis       = None,
        confidence   = 0.7,
    ).model_dump(mode="json")


def _state(*, tickers: list[str], decision: dict | None) -> dict:
    """Compose the minimal session state the enricher reads from."""
    return {
        "tickers":             tickers,
        "portfolio":           Portfolio(cash=1000.0).model_dump(mode="json"),
        "tick_id":             "t-test",
        "strategist_decision": decision,
    }


# ---------------------------------------------------------------------------
# Happy path — the regression case for the schema-retry-eats-enrichment bug
# ---------------------------------------------------------------------------

def test_enricher_transforms_narrow_llm_output_into_full_decision():
    """Given the narrow LLM output in state, the enricher yields one Event
    whose state_delta carries the full StrategistDecision with derived fields.

    REGRESSION: when the LlmAgent's after_agent_callback was the enrichment
    site, a schema-retry inside RetryingAgentWrapper could leave only the
    LLM's narrow output in state.  RiskGate then read ``decision.target_weights``
    and got ``{}`` (schema default), produced zero orders, and the executor
    asserted ``open without fill price`` for every open stance.  This test
    locks in the new contract: the enricher runs unconditionally after the
    wrapper and produces the full shape downstream agents need.
    """
    tickers = ["AAPL", "MSFT", "NVDA"]
    narrow = _narrow_llm_output(tickers)

    # The narrow shape must NOT contain target_weights — this is the
    # pre-enrichment state that downstream agents currently see (and break on).
    assert "target_weights" not in narrow
    assert "close_reasons" not in narrow

    state = _state(tickers=tickers, decision=narrow)

    enricher = StrategistEnricher()
    events = _run(enricher._run_async_impl(_StubCtx(state)))

    # The enricher yields exactly one Event carrying the enriched decision
    # via state_delta (contract Rule 1 — no in-place state mutation).
    assert len(events) == 1, f"expected one event, got {len(events)}"
    delta = events[0].actions.state_delta
    assert "strategist_decision" in delta

    enriched = delta["strategist_decision"]
    assert isinstance(enriched, dict)
    # All derived dicts must be present and populated.
    assert "target_weights" in enriched
    assert "close_reasons" in enriched
    assert "trim_reasons" in enriched
    # Open stance on AAPL → target_weights["AAPL"] == 0.05; flat tickers
    # padded to 0.0 by the active-stances derivation carry-forward.
    assert enriched["target_weights"]["AAPL"] == pytest.approx(0.05)
    for t in ("MSFT", "NVDA"):
        assert enriched["target_weights"][t] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

def test_enricher_no_op_when_no_decision_in_state():
    """If state has no strategist_decision, the enricher yields nothing.

    Mirrors StrategistDecisionWriter's no-op short-circuit — a missing
    decision is normal on ticks where the strategist did not run (e.g.
    the cold-start path before the first scheduled tick).
    """
    state = _state(tickers=["AAPL"], decision=None)
    enricher = StrategistEnricher()
    events = _run(enricher._run_async_impl(_StubCtx(state)))
    assert events == []


# ---------------------------------------------------------------------------
# Contract enforcement — must still raise loudly
# ---------------------------------------------------------------------------

def test_enricher_raises_on_off_watchlist_ticker():
    """The enricher inherits the off-watchlist contract from the old callback:
    if the LLM emits a stance for a ticker not in the watchlist, abort the tick.
    """
    tickers = ["AAPL"]
    narrow = StrategistLLMDecision(
        stances=[
            TickerStance(
                ticker       = "ZZZZ",                                            # off-watchlist
                intent       = "open",
                weight       = 0.05,
                rationale    = "fictional ticker",
                horizon      = "swing",
                target_price = 100.0,
                stop_price   = 90.0,
            ),
        ],
        decision_tag = "bad",
        reasoning    = "off-watchlist",
        thesis       = None,
        confidence   = 0.7,
    ).model_dump(mode="json")

    state = _state(tickers=tickers, decision=narrow)
    enricher = StrategistEnricher()

    with pytest.raises(StrategistContractViolation, match="off-watchlist"):
        _run(enricher._run_async_impl(_StubCtx(state)))
