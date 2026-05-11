"""Tick initial-state seeding tests — Tier 1, no LLM, no ADK runner."""
from __future__ import annotations

import asyncio

from broker.fake import FakeBroker
from broker.portfolio import Position
from orchestrator.tick import _build_initial_state


def test_initial_state_seeds_portfolio_from_broker():
    """`run_once`'s initial state must carry a live portfolio dump so the strategist
    sees real holdings on the first tick."""
    broker = FakeBroker(starting_cash=1_000.0, prices={"AAPL": 200.0})
    # Pre-fill the broker so we have a real position to assert on.
    broker._positions["AAPL"] = Position(quantity=2.0, avg_cost=180.0, last_price=200.0)
    state = asyncio.run(_build_initial_state(broker, "tick-X", ["AAPL"]))
    assert "portfolio" in state
    assert state["portfolio"]["cash"] == 1_000.0
    assert "AAPL" in state["portfolio"]["positions"]


def test_initial_state_retains_required_keys():
    """Seeding portfolio must not drop any of the keys the pipeline depends on."""
    broker = FakeBroker(starting_cash=500.0, prices={})
    state = asyncio.run(_build_initial_state(broker, "tick-Y", ["MSFT"]))
    for key in ("tick_id", "tickers", "memory_buffer", "day_digest", "thesis", "positions", "portfolio"):
        assert key in state
    assert state["tick_id"] == "tick-Y"
    assert state["tickers"] == ["MSFT"]
    assert state["positions"] == {}
