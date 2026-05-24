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

    NOTE (Spec B / Band 2): ``positions`` and ``thesis`` are intentionally
    absent from the initial state dict — they have migrated to ADK user-scoped
    state (``user:positions``, ``user:thesis``) and are hydrated by
    ``DatabaseSessionService`` on session create rather than being seeded here.
    """
    broker = FakeBroker(starting_cash=500.0, prices={})
    state = asyncio.run(_build_initial_state(broker, "tick-Y", ["MSFT"]))

    # Keys that must always be present in the initial state.
    for key in ("tick_id", "tickers", "memory_buffer", "day_digest", "portfolio"):
        assert key in state, f"Expected key {key!r} missing from initial state"

    assert state["tick_id"] == "tick-Y"
    assert state["tickers"] == ["MSFT"]

    # ``positions`` and ``thesis`` must NOT be seeded bare — they are user-scoped
    # ADK state, not per-tick seeds.  Seeding them here would shadow the DB row.
    assert "positions" not in state, (
        "'positions' should not be seeded in initial state; use user:positions via ADK"
    )
    assert "thesis" not in state, (
        "'thesis' should not be seeded in initial state; use user:thesis via ADK"
    )
