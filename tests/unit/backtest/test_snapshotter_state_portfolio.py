"""Test that SnapshotterAgent reads ``state["portfolio"]`` rather than calling
``broker.get_portfolio()`` — audit fix A-072.

Phase 2 canonicalises the portfolio into ``state["portfolio"]`` on every tick,
so the Snapshotter (which runs at Phase 7) should consume that value directly
rather than issuing a redundant broker round-trip mid-tick.

The test drives the agent through the same synchronous ``_run`` helper and
``_StubCtx`` stand-in used in ``test_wall_clock_leakage.py``, keeping both
files' driving style consistent and avoiding event-loop conflicts.

Hermetic SPY fetch
------------------
The agent fetches SPY via ``from data import get_price_history`` at call-time
inside ``_run_async_impl``.  Patching ``sys.modules["yfinance"]`` is a NO-OP
against that seam — the agent never imports yfinance directly.  We patch
``data.get_price_history`` instead, which is the exact name the agent resolves,
and return a deterministic ``PriceHistory`` with a finite close so the product-
level non-finite-close guard does not fire.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch


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


# ── Test ───────────────────────────────────────────────────────────────────────

def test_snapshotter_uses_state_portfolio_not_broker() -> None:
    """Snapshotter reads ``state["portfolio"]``, never calls ``broker.get_portfolio`` (A-072).

    Two portfolios are constructed with diverging cash values.  The canonical
    portfolio (cash=100.0) is placed in ``state["portfolio"]``; the broker probe
    returns a different portfolio (cash=999.0).  After the agent runs:

    - ``state["last_snapshot"]["bot_cash"]`` must equal 100.0 (state value wins).
    - ``broker.get_portfolio`` must never have been called.

    The SPY fetch is patched at ``data.get_price_history`` — the seam the agent
    actually uses — returning a deterministic bar with a finite close so the
    non-finite-close guard introduced in the loud-fail audit does not trigger.
    """
    from agents.snapshot.agent import SnapshotterAgent
    from broker.portfolio import Portfolio
    from data.models.market import OHLCBar
    from data.models.price_history import PriceHistory

    # Deterministic SPY bar — a finite close is required so the product guard
    # does not raise and abort the run.
    _AS_OF = datetime(2023, 3, 10, 9, 30, 0, tzinfo=UTC)
    spy_bar = OHLCBar(
        timestamp=_AS_OF,
        open=398.0,
        high=402.0,
        low=397.0,
        close=400.0,
        volume=1_000_000.0,
    )
    spy_history = PriceHistory(ticker="SPY", bars=[spy_bar])

    # Canonical portfolio that Phase 2 would have published into state.
    canonical_portfolio = Portfolio(cash=100.0)

    # Divergent broker probe — if the agent calls this, the cash value will be
    # 999.0 and the assertion below will catch the mistake.
    broker = MagicMock()
    broker.get_portfolio = AsyncMock(return_value=Portfolio(cash=999.0))

    state: dict = {
        "tick_id": "t-1",
        "as_of":   _AS_OF,
        # Phase 2 canonical snapshot — agent should read this, not the broker.
        "portfolio": canonical_portfolio.model_dump(mode="json"),
    }

    # Patch the seam the agent actually uses — not sys.modules["yfinance"].
    with patch("data.get_price_history", new=AsyncMock(return_value=spy_history)):
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
