"""derive_legacy_fields tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.derivation import (
    TickContext,
    derive_legacy_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    prices: dict[str, float] | None = None,
    weights: dict[str, float] | None = None,
) -> TickContext:
    """Build a minimal TickContext for test use.

    Parameters
    ----------
    prices:
        Optional mapping of ticker → current market price.
    weights:
        Optional mapping of ticker → current portfolio weight.

    Returns
    -------
    TickContext
        A TickContext seeded with ``tick_id="tick_X"``, ``decision_tag="x"``,
        and a fixed datetime of 2026-05-08 14:00 UTC.
    """
    return TickContext(
        tick_id="tick_X",
        decision_tag="x",
        now=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        current_prices=prices or {},
        current_weights=weights or {},
    )


def test_open_creates_position_thesis():
    """An open stance (current weight 0, preferred > 0) should create a PositionThesis."""
    stance = TickerStance(
        ticker="AAPL",
        preferred_weight=0.08,
        conviction=0.7,
        rationale="open",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
    )
    ctx = _ctx(prices={"AAPL": 200.0}, weights={})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.08}
    assert "AAPL" in out.new_positions

    pt = out.new_positions["AAPL"]
    assert pt.opened_at == ctx.now
    assert pt.opened_price == 200.0
    assert pt.opened_tag == "x"
    assert pt.opened_tick_id == "tick_X"
    assert pt.target_price == 210.0

    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_close_records_close_reason():
    """A close stance (preferred_weight 0) should record the close_reason."""
    stance = TickerStance(
        ticker="AAPL",
        preferred_weight=0.0,
        conviction=0.5,
        rationale="exit",
        close_reason="thesis broken",
    )
    ctx = _ctx(weights={"AAPL": 0.08})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.0}
    assert out.close_reasons == {"AAPL": "thesis broken"}
    assert out.new_positions == {}
    assert out.trim_reasons == {}


def test_trim_records_trim_reason():
    """A trim stance (preferred_weight below current by >δ) should record the trim_reason."""
    stance = TickerStance(
        ticker="MSFT",
        preferred_weight=0.05,
        conviction=0.5,
        rationale="reduce",
        trim_reason="lock in profits",
    )
    ctx = _ctx(weights={"MSFT": 0.12})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"MSFT": 0.05}
    assert out.trim_reasons == {"MSFT": "lock in profits"}
    assert out.new_positions == {}
    assert out.close_reasons == {}


def test_hold_yields_only_target_weight():
    """A hold stance (no meaningful weight change) should only populate target_weights."""
    stance = TickerStance(
        ticker="MSFT",
        preferred_weight=0.06,
        conviction=0.5,
        rationale="hold",
    )
    ctx = _ctx(weights={"MSFT": 0.06})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"MSFT": 0.06}
    assert out.new_positions == {}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_multiple_stances_aggregate_correctly():
    """Multiple stances are each dispatched to the correct output bucket."""
    stances = [
        TickerStance(
            ticker="AAPL",
            preferred_weight=0.08,
            conviction=0.7,
            rationale="open",
            horizon="swing",
            target_price=210.0,
            stop_price=185.0,
        ),
        TickerStance(
            ticker="MSFT",
            preferred_weight=0.0,
            conviction=0.5,
            rationale="exit",
            close_reason="rotate",
        ),
        TickerStance(
            ticker="NVDA",
            preferred_weight=0.05,
            conviction=0.5,
            rationale="trim",
            trim_reason="overweight",
        ),
    ]
    ctx = _ctx(
        prices={"AAPL": 200.0, "MSFT": 410.0, "NVDA": 850.0},
        weights={"MSFT": 0.10, "NVDA": 0.15},
    )
    out = derive_legacy_fields(stances, ctx)

    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.0, "NVDA": 0.05}
    assert "AAPL" in out.new_positions
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {"NVDA": "overweight"}


def test_open_falls_back_to_zero_when_no_price():
    """If the prices dict has no entry for the ticker, opened_price defaults to 0.0.

    This is intentional — the caller is responsible for ensuring the prices
    dict is populated.  The derivation function does not raise.
    """
    stance = TickerStance(
        ticker="AAPL",
        preferred_weight=0.08,
        conviction=0.7,
        rationale="open",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
    )
    ctx = _ctx(prices={}, weights={})
    out = derive_legacy_fields([stance], ctx)

    assert out.new_positions["AAPL"].opened_price == 0.0


def test_add_action_only_populates_target_weight():
    """An add stance (preferred_weight > current by >δ but both above ε) should only
    populate target_weights — no new_positions, no close_reasons, no trim_reasons.

    The 'add' action adds to an existing position; it doesn't open a fresh one,
    so PositionThesis is not created here (that was created on the original open).
    """
    stance = TickerStance(
        ticker="AAPL",
        preferred_weight=0.15,
        conviction=0.8,
        rationale="add to winner",
    )
    # Current weight 0.08 → preferred 0.15; difference 0.07 > SIZE_CHANGE_EPSILON (0.02)
    # → derive_lifecycle_action returns "add"
    ctx = _ctx(prices={"AAPL": 210.0}, weights={"AAPL": 0.08})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.15}
    assert out.new_positions == {}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}
