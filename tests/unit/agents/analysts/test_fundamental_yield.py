"""Contract Rule 1 test — Fundamental analyst yields evidence via state_delta.

A2.5 wraps the FundamentalAnalyst LlmAgent in a thin BaseAgent that
yields a single ``Event(state_delta={"fundamental_evidence": [...]})``
after the LlmAgent's after_agent_callback has built the evidence list.

This test wires the wrapper, fakes the inner LlmAgent's state writes,
and asserts the outer wrapper emits the evidence on a state_delta.
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agents.analysts._base_yield import YieldingAnalystWrapper


def test_wrapper_yields_evidence_on_state_delta() -> None:
    """The wrapper must yield one Event with the evidence list on state_delta."""
    # A toy inner agent: writes a fake evidence list to state and yields no events.
    class _InnerNoEvents:
        name = "FundamentalAnalyst"
        async def run_async(self, _ctx):
            # Simulate the LlmAgent's after-agent-callback writing evidence.
            _ctx.session.state["fundamental_evidence"] = [
                {"ticker": "AAPL", "analyst": "fundamental"},
            ]
            if False:  # pragma: no cover — keep this an async generator
                yield None

    wrapper = YieldingAnalystWrapper(
        name="FundamentalAnalystBranch",
        inner=_InnerNoEvents(),
        evidence_state_key="fundamental_evidence",
    )

    fake_session = MagicMock()
    fake_session.state = {"tickers": ["AAPL"]}
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-2"
    fake_ctx.session = fake_session

    async def _drain() -> list:
        out = []
        async for ev in wrapper._run_async_impl(fake_ctx):
            out.append(ev)
        return out

    events = asyncio.run(_drain())
    assert len(events) == 1
    delta = events[0].actions.state_delta
    assert "fundamental_evidence" in delta
    assert delta["fundamental_evidence"][0]["ticker"] == "AAPL"
