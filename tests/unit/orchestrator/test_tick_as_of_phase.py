"""Contract test: live ``_build_initial_state`` must seed ``as_of`` +
``tick_phase``.

Backtest already seeds both at ``src/backtest/driver.py:194-195``.  Live
historically omitted them and relied on the
``resolve_as_of(..., allow_wallclock=True)`` fallback at every consumer.
Contract Rule 7 ("lifecycle owns Phase 2 hydration") demands a single
authoritative writer; seeding once in the builder closes the asymmetry.

"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.tick import _build_initial_state


@pytest.mark.asyncio
async def test_build_initial_state_seeds_as_of_and_tick_phase() -> None:
    """``_build_initial_state`` must populate both ``as_of`` (an ISO-8601
    string representing a timezone-aware UTC instant near wall-clock) and
    ``tick_phase`` (the literal string ``"live"``) in the returned state dict."""

    # Mock broker: ``get_portfolio`` returns a portfolio whose ``model_dump``
    # produces a serialisable dict.  ``MagicMock`` proxies cover the rest.
    broker = MagicMock()
    portfolio = MagicMock()
    portfolio.model_dump.return_value = {"cash": 0.0, "positions": {}}
    broker.get_portfolio = AsyncMock(return_value=portfolio)

    # Patch the reference-price fetch so the test doesn't touch yfinance.
    with patch(
        "orchestrator.tick._fetch_reference_prices",
        new=AsyncMock(return_value={}),
    ):
        before = datetime.now(tz=UTC)
        state = await _build_initial_state(
            broker, tick_id="tick-test-001", tickers=["AAPL"],
        )
        after = datetime.now(tz=UTC)

    # ``as_of`` must be present as an ISO-8601 string.  Plan 04 mandates
    # ISO-coercion at the live state-boundary — ``DatabaseSessionService``
    # cannot persist raw datetime objects, and parity with the backtest
    # lifecycle requires both writers to emit the same shape.  Consumers
    # call ``data.timeguard.resolve_as_of`` which round-trips the string
    # back to a tz-aware ``datetime``.
    assert "as_of" in state, "live builder must seed state['as_of']"
    as_of_raw = state["as_of"]
    assert isinstance(as_of_raw, str), (
        f"as_of must be ISO-stringified at the state boundary; "
        f"got {type(as_of_raw).__name__!r} = {as_of_raw!r}"
    )

    # Parse back and confirm tz-aware UTC + within the wall-clock window
    # the test captured.  Five seconds of slack covers any in-process drift.
    as_of = datetime.fromisoformat(as_of_raw)
    assert as_of.tzinfo is not None, "as_of must be timezone-aware"
    assert as_of.utcoffset() == timedelta(0), "as_of must be in UTC"
    assert before - timedelta(seconds=5) <= as_of <= after + timedelta(seconds=5), (
        f"as_of {as_of} must be within wall-clock window [{before}, {after}]"
    )

    # ``tick_phase`` must be the literal string ``"live"``.
    assert state.get("tick_phase") == "live", (
        f"live builder must seed tick_phase='live'; got {state.get('tick_phase')!r}"
    )
