from unittest.mock import MagicMock, patch

import pytest

from agents.snapshot.agent import build_snapshotter
from broker.fake import FakeBroker


def _make_ctx(state: dict) -> MagicMock:
    """Build a mock InvocationContext that satisfies the agent's needs.

    The snapshotter now yields an ``Event`` whose ``invocation_id`` field is
    a Pydantic-validated string, so the mock must return a real string
    rather than the default ``MagicMock`` attribute proxy.
    """

    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_snapshotter_writes_state():
    broker = FakeBroker(starting_cash=10_000.0, prices={})
    snapper = build_snapshotter(broker)
    state = {"tick_id": "tick-001"}
    ctx = _make_ctx(state)
    with patch("yfinance.Ticker") as mock_yf:
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = MagicMock(
            empty=False,
            **{"__getitem__": lambda self, key: MagicMock(**{"iloc.__getitem__": lambda s, i: 470.0})}
        )
        mock_yf.return_value = mock_ticker
        async for _ in snapper._run_async_impl(ctx):
            pass

    assert "last_snapshot" in state
    snap = state["last_snapshot"]
    assert snap["bot_total_value"] == 10_000.0
    assert snap["tick_id"] == "tick-001"
