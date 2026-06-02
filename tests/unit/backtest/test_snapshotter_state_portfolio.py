"""Test that SnapshotterAgent reads ``state["portfolio"]`` rather than calling
``broker.get_portfolio()`` — audit fix A-072.

Phase 2 canonicalises the portfolio into ``state["portfolio"]`` on every tick,
so the Snapshotter (which runs at Phase 7) should consume that value directly
rather than issuing a redundant broker round-trip mid-tick.

The test drives the agent through the same synchronous ``_run`` helper and
``_StubCtx`` stand-in used in ``test_wall_clock_leakage.py``, keeping both
files' driving style consistent and avoiding event-loop conflicts.
"""
from __future__ import annotations

import asyncio
import sys
import types

# ── Minimal ADK stand-ins (self-contained; mirrored from test_wall_clock_leakage) ─

class _StubCtx:
    """Minimal InvocationContext stand-in — exposes only session.state and
    invocation_id, which are all the SnapshotterAgent accesses.
    """

    def __init__(self, state: dict) -> None:
        class _S:
            pass

        self.session = _S()
        self.session.state = state
        self.invocation_id = "test-invocation"


def _run(coro_gen) -> list:
    """Drain an async generator synchronously and return the collected events."""

    async def _drain():
        return [ev async for ev in coro_gen]

    return asyncio.run(_drain())


# ── yfinance stub builder ──────────────────────────────────────────────────────

def _install_fake_yfinance() -> None:
    """Inject a stub ``yfinance`` module so the Snapshotter stays offline.

    The agent fetches SPY via ``get_price_history``, which ultimately calls
    ``yfinance.Ticker(...).history()``.  We return an empty DataFrame-like
    mock so the agent degrades gracefully to ``spy_price=0.0`` without any
    network call.
    """
    from unittest.mock import MagicMock

    fake_yf = types.ModuleType("yfinance")
    fake_ticker = MagicMock()
    # Returning empty=True causes the spy_price fallback path to trigger
    # (``if spy_hist.bars`` evaluates False, so spy_price stays 0.0).
    fake_ticker.history.return_value = MagicMock(empty=True)
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    sys.modules["yfinance"] = fake_yf


# ── Test ───────────────────────────────────────────────────────────────────────

def test_snapshotter_uses_state_portfolio_not_broker() -> None:
    """Snapshotter reads ``state["portfolio"]``, never calls ``broker.get_portfolio`` (A-072).

    Two portfolios are constructed with diverging cash values.  The canonical
    portfolio (cash=100.0) is placed in ``state["portfolio"]``; the broker probe
    returns a different portfolio (cash=999.0).  After the agent runs:

    - ``state["last_snapshot"]["bot_cash"]`` must equal 100.0 (state value wins).
    - ``broker.get_portfolio`` must never have been called.
    """
    from unittest.mock import AsyncMock, MagicMock

    from agents.snapshot.agent import SnapshotterAgent
    from broker.portfolio import Portfolio

    # Canonical portfolio that Phase 2 would have published into state.
    canonical_portfolio = Portfolio(cash=100.0)

    # Divergent broker probe — if the agent calls this, the cash value will be
    # 999.0 and the assertion below will catch the mistake.
    broker = MagicMock()
    broker.get_portfolio = AsyncMock(return_value=Portfolio(cash=999.0))

    state: dict = {
        "tick_id":   "t-1",
        # Phase 2 canonical snapshot — agent should read this, not the broker.
        "portfolio": canonical_portfolio.model_dump(mode="json"),
    }

    _install_fake_yfinance()

    # db_session=None skips the persistence path entirely.
    agent = SnapshotterAgent(broker=broker, db_session=None)
    _run(agent._run_async_impl(_StubCtx(state)))

    # A-072: the broker round-trip must be gone.
    broker.get_portfolio.assert_not_called()

    # The snapshot must reflect the canonical portfolio's cash value.
    assert "last_snapshot" in state, "Snapshotter did not write state['last_snapshot']"
    assert state["last_snapshot"]["bot_cash"] == 100.0, (
        f"Expected bot_cash=100.0 from state portfolio, "
        f"got {state['last_snapshot']['bot_cash']}"
    )
