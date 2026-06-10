"""Unit tests for the TickState Pydantic model in ``orchestrator.state``.

These tests exercise real Pydantic validation — not just default values — so
schema drift (wrong type, removed field, broken validator) surfaces as a hard
failure rather than a silent degradation at runtime.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from orchestrator.state import TickState


def test_tick_state_defaults():
    """Default-constructed TickState must carry the documented default values.

    These are the contract invariants the pipeline relies on before each
    phase writes its keys.
    """
    ts = TickState()

    assert ts.tick_id == ""
    assert ts.tickers == []
    assert ts.memory_buffer == []
    assert ts.last_executed_tick_id is None


def test_tick_state_serializes():
    """model_dump must round-trip all supplied fields correctly.

    This is a basic sanity check that the Pydantic model produces the dict
    shape the rest of the pipeline expects when serialised.
    """
    ts = TickState(tick_id="tick-001", tickers=["AAPL", "MSFT"])
    data = ts.model_dump()

    assert data["tick_id"] == "tick-001"
    assert "AAPL" in data["tickers"]


def test_tick_state_stores_supplied_field_values():
    """Pydantic must persist explicitly supplied field values, not silently
    replace them with defaults.

    This rules out a class of silent-degradation bugs where a validator or
    default_factory overrides the caller's value without raising.
    """
    ts = TickState(
        tick_id="tick-content-assert",
        tickers=["TSLA", "NVDA"],
        day_digest="earnings season winding down",
        last_executed_tick_id="tick-prev",
    )

    # Content assertions: each field must carry the supplied value exactly.
    assert ts.tick_id == "tick-content-assert"
    assert "TSLA" in ts.tickers
    assert "NVDA" in ts.tickers
    assert ts.day_digest == "earnings season winding down"
    assert ts.last_executed_tick_id == "tick-prev"


def test_tick_state_rejects_invalid_ticker_list_type():
    """TickState must raise ``ValidationError`` when ``tickers`` is given a
    non-list value (e.g. a plain string).

    This guards against callers accidentally passing a single ticker string
    instead of a list — a mistake that would iterate over characters rather
    than symbols if it passed through unchecked.
    """
    with pytest.raises(ValidationError):
        # A plain string is not a valid list[str] for the tickers field.
        TickState(tickers="AAPL")  # type: ignore[arg-type]


def test_tick_state_json_round_trip_preserves_content():
    """``model_dump(mode='json')`` followed by ``model_validate`` must produce
    an identical object.

    The pipeline serialises state through ADK's session service (which calls
    ``json.dumps`` internally), so every field must survive a JSON round-trip
    with no loss or mutation.
    """
    original = TickState(
        tick_id="tick-rt",
        tickers=["AMZN", "GOOG"],
        day_digest="tech rally continues",
        last_executed_tick_id="tick-rt-prev",
    )
    dumped   = original.model_dump(mode="json")
    rebuilt  = TickState.model_validate(dumped)

    # The rebuilt model must match the original on all key fields.
    assert rebuilt.tick_id == original.tick_id
    assert rebuilt.tickers == original.tickers
    assert rebuilt.day_digest == original.day_digest
    assert rebuilt.last_executed_tick_id == original.last_executed_tick_id
