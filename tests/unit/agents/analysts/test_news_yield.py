"""Contract Rule 1 test — News analyst yields evidence via state_delta."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from agents.analysts._base_yield import YieldingAnalystWrapper


def test_news_wrapper_yields_evidence_on_state_delta() -> None:
    """News wrapper yields the evidence list on state_delta after the inner agent."""
    class _Inner:
        name = "NewsAnalyst"
        async def run_async(self, _ctx):
            _ctx.session.state["news_evidence"] = [
                {"ticker": "AAPL", "analyst": "news"},
            ]
            if False:  # pragma: no cover
                yield None

    wrapper = YieldingAnalystWrapper(
        name="NewsAnalystBranch", inner=_Inner(),
        evidence_state_key="news_evidence",
    )

    fake_session = MagicMock()
    fake_session.state = {"tickers": ["AAPL"]}
    fake_ctx = MagicMock()
    fake_ctx.invocation_id = "inv-3"
    fake_ctx.session = fake_session

    async def _drain() -> list:
        out = []
        async for ev in wrapper._run_async_impl(fake_ctx):
            out.append(ev)
        return out

    events = asyncio.run(_drain())
    assert len(events) == 1
    assert events[0].actions.state_delta["news_evidence"][0]["ticker"] == "AAPL"
