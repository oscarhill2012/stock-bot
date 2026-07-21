"""Tick initial-state seeding tests — Tier 1, no LLM, no ADK runner."""
from __future__ import annotations

import asyncio

from broker.fake import FakeBroker
from broker.portfolio import Position
from orchestrator.tick import _build_initial_state


def test_initial_state_seeds_portfolio_from_broker():
    """`run_once`'s initial state must carry a live portfolio dump so the strategist
    sees real holdings on the first tick.
    """
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    # Pre-fill the broker so we have a real position to assert on.
    broker._positions["AAPL"] = Position(quantity=2.0, avg_cost=180.0, last_price=200.0)
    state = asyncio.run(_build_initial_state(broker, "tick-X", ["AAPL"]))
    assert "portfolio" in state
    assert state["portfolio"]["cash"] == 1_000.0
    assert "AAPL" in state["portfolio"]["positions"]


def test_initial_state_retains_required_keys():
    """Seeding portfolio must not drop any of the keys the pipeline depends on.

    NOTE (Spec B / Band 2): ``positions`` is intentionally absent from the
    initial state dict — it has migrated to ADK user-scoped state
    (``user:positions``) and is hydrated by ``DatabaseSessionService`` on
    session create rather than being seeded here.  The portfolio-level
    ``thesis`` string was removed entirely (C4) and is no longer part of the
    pipeline at all.
    """
    broker = FakeBroker(starting_cash=500.0, prices={})
    state = asyncio.run(_build_initial_state(broker, "tick-Y", ["MSFT"]))

    # Keys that must always be present in the initial state.
    for key in ("tick_id", "tickers", "portfolio"):
        assert key in state, f"Expected key {key!r} missing from initial state"

    assert state["tick_id"] == "tick-Y"
    assert state["tickers"] == ["MSFT"]

    # ``positions`` must NOT be seeded bare — it is user-scoped ADK state, not
    # a per-tick seed.  Seeding it here would shadow the DB row.  ``thesis``
    # must never appear at all — the portfolio-level thesis field was removed
    # entirely in C4 (there is no user:thesis to bridge to any more).
    assert "positions" not in state, (
        "'positions' should not be seeded in initial state; use user:positions via ADK"
    )
    assert "thesis" not in state, (
        "'thesis' must never appear — the portfolio-level thesis field was removed (C4)"
    )
