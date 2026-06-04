"""Unit tests for executor state-write / state_delta parity (A-069 / A-073).

Verifies the asymmetry between the two durable executor keys:

- ``last_executed_tick_id``: delta-only.  Its only reader is the idempotency
  guard at the *start* of ``_run_async_impl`` on the NEXT tick; no same-tick
  agent reads it after the executor writes it.  A direct in-tick write is
  therefore dead bookkeeping (A-069).

- ``executions``: paired (direct write + delta).  The decision-logger reads
  ``state["executions"]`` from the shared in-tick state dict within the SAME
  tick (``dl.on_executions(dict(state))``), so the direct write is required
  (A-073).  The delta carries it forward to the next tick.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agents.executor.agent import ExecutorAgent
from broker.fake import FakeBroker
from orchestrator.state import Order

# ── Context stub (mirrors test_open_positions_state.py) ──────────────────────


class _StubCtx:
    """Minimal ADK InvocationContext stand-in wrapping a plain dict as session state.

    Carries a real ``invocation_id`` string because the executor's yielded
    ``Event`` is Pydantic-validated and rejects ``None``.

    Parameters
    ----------
    state:
        The session state dict to expose.
    """

    def __init__(self, state: dict) -> None:
        session = MagicMock()
        session.state = state
        self.session = session
        self.invocation_id = "test-invocation-parity"


async def _run(agent: ExecutorAgent, state: dict) -> list:
    """Drive ``_run_async_impl`` to completion and return all yielded Events.

    Parameters
    ----------
    agent:
        The ``ExecutorAgent`` under test.
    state:
        Session state dict mutated in-place by the executor (mirrors the
        shared-object-reference behaviour of the real ADK runner).

    Returns
    -------
    list[Event]
        All events emitted by the generator.
    """
    ctx = _StubCtx(state)
    events = []
    async for ev in agent._run_async_impl(ctx):
        events.append(ev)
    return events


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_executor_last_tick_id_is_delta_only():
    """``last_executed_tick_id`` reaches the next tick only via the Event
    state_delta (A-069): the executor does NOT write it into in-tick state,
    because the idempotency guard reads it at the *next* tick's start, never
    same-tick.  ``executions`` stays paired (direct + delta) because the
    decision-logger reads it from in-tick state within the same tick (A-073).
    """
    tick_id = "tick-parity-001"
    fill_price = 150.0

    broker = FakeBroker(starting_cash=10_000.0, prices={"MSFT": fill_price})
    executor = ExecutorAgent(broker=broker)

    # State without ``last_executed_tick_id`` — simulates a fresh tick.
    state: dict = {
        "tick_id":    tick_id,
        "as_of":      "2026-04-15T14:00:00+00:00",
        "final_orders": [
            Order(ticker="MSFT", action="BUY", quantity=3.0, est_price=fill_price),
        ],
        "user:positions": {},
        "strategist_decision": {
            "decision_tag": "buy_msft",
            "stances": [
                {
                    "ticker":    "MSFT",
                    "intent":    "buy",
                    "weight":    0.04,
                    "rationale": "strong upward momentum",
                },
            ],
        },
    }

    events = await _run(executor, state)

    assert len(events) == 1, "BUY should yield exactly one Event"

    delta = events[0].actions.state_delta

    # Both durable keys must travel cross-tick via the delta.
    assert delta["last_executed_tick_id"] == tick_id, (
        "last_executed_tick_id must appear in state_delta for cross-tick propagation"
    )
    assert "executions" in delta, (
        "executions must appear in state_delta for cross-tick propagation"
    )

    # executions is ALSO written directly to in-tick state so the decision-logger
    # can read it from the shared dict this same tick (A-073).
    assert state["executions"] == delta["executions"], (
        "executions must be written to both in-tick state (for decision-logger) "
        "and state_delta (for cross-tick persistence)"
    )

    # last_executed_tick_id is NOT written directly to in-tick state — delta-only
    # (A-069): its only reader is the idempotency guard on the *next* tick.
    assert "last_executed_tick_id" not in state, (
        "last_executed_tick_id must NOT be written to in-tick state — "
        "it is delta-only (A-069); the in-tick direct write was dead bookkeeping"
    )
