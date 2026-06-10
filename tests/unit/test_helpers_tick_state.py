"""Self-tests for tests/_helpers/tick_state.py."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tests._helpers import make_tick_state


def test_minimal_invocation_populates_required_keys():
    """A minimal call yields a state dict with all §A keys present."""
    state = make_tick_state(watchlist=["AAPL", "MSFT"])
    # Per docs/contract-invariants.md §A.
    for key in (
        "as_of",
        "watchlist",
        "user:positions",
        "temp:_positions",
        "reference_prices",
        "portfolio",
        "temp:_trace",
        "temp:_decision_logger",
    ):
        assert key in state, f"missing required key: {key}"


def test_as_of_is_iso_string():
    """as_of MUST be ISO-stringified (feedback_as_of_boundary_coercion)."""
    state = make_tick_state(watchlist=["AAPL"])
    assert isinstance(state["as_of"], str)
    # Round-trip parse must succeed.
    datetime.fromisoformat(state["as_of"])


def test_held_positions_populate_both_keys():
    """held=dict populates user:positions and temp:_positions identically."""
    state = make_tick_state(watchlist=["AAPL"], held={"AAPL": 10.0})
    assert state["user:positions"]["AAPL"]["qty"] == 10.0
    assert state["temp:_positions"]["AAPL"]["qty"] == 10.0


def test_reference_prices_default_covers_watchlist_and_held():
    """If reference_prices is None, defaults cover watchlist ∪ held."""
    state = make_tick_state(watchlist=["AAPL"], held={"MSFT": 5.0})
    assert "AAPL" in state["reference_prices"]
    assert "MSFT" in state["reference_prices"]


def test_no_bare_positions_key():
    """The bare ``positions`` key is forbidden (Plan 03 / A-070)."""
    state = make_tick_state(watchlist=["AAPL"])
    assert "positions" not in state, "bare 'positions' is forbidden"
