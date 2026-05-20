"""derive_legacy_fields tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.derivation import (
    TickContext,
    derive_legacy_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    weights: dict[str, float] | None = None,
) -> TickContext:
    """Build a minimal TickContext for test use.

    Parameters
    ----------
    weights:
        Optional mapping of ticker → current portfolio weight.

    Returns
    -------
    TickContext
        A TickContext seeded with ``tick_id="tick_X"``, ``decision_tag="x"``,
        and a fixed datetime of 2026-05-08 14:00 UTC.

    Notes
    -----
    ``current_prices`` is no longer carried on ``TickContext`` — the strategist
    deliberately does not stamp ``opened_price`` on freshly-opened positions;
    the executor does that post-fill.  See ``PositionThesis`` docstring for
    the responsibility split.
    """
    return TickContext(
        tick_id="tick_X",
        decision_tag="x",
        now=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
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
    ctx = _ctx(weights={})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.08}
    assert "AAPL" in out.new_positions

    pt = out.new_positions["AAPL"]
    assert pt.opened_at == ctx.now
    # ``opened_price`` is deliberately left unset by the strategist — the
    # executor stamps the real fill price post-BUY.  See PositionThesis docs.
    assert pt.opened_price is None
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
    """A trim stance (preferred_weight below current by >δ) should record the trim_reason.

    Non-zero stances must also carry the lifecycle hint fields (horizon,
    target_price, stop_price) per ``TickerStance._require_lifecycle_hints_on_nonzero``
    — a trim is still holding capital and so still needs an exit discipline.
    """
    stance = TickerStance(
        ticker="MSFT",
        preferred_weight=0.05,
        conviction=0.5,
        rationale="reduce",
        horizon="swing",
        target_price=450.0,
        stop_price=395.0,
        trim_reason="lock in profits",
    )
    ctx = _ctx(weights={"MSFT": 0.12})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"MSFT": 0.05}
    assert out.trim_reasons == {"MSFT": "lock in profits"}
    assert out.new_positions == {}
    assert out.close_reasons == {}


def test_hold_yields_only_target_weight():
    """A hold stance (no meaningful weight change) should only populate target_weights.

    Even on a pure hold, the strategist is still holding capital, so the
    schema-level validator requires horizon/target_price/stop_price.
    """
    stance = TickerStance(
        ticker="MSFT",
        preferred_weight=0.06,
        conviction=0.5,
        rationale="hold",
        horizon="swing",
        target_price=450.0,
        stop_price=395.0,
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
            horizon="swing",
            target_price=950.0,
            stop_price=800.0,
            trim_reason="overweight",
        ),
    ]
    ctx = _ctx(weights={"MSFT": 0.10, "NVDA": 0.15})
    out = derive_legacy_fields(stances, ctx)

    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.0, "NVDA": 0.05}
    assert "AAPL" in out.new_positions
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {"NVDA": "overweight"}


def test_open_leaves_opened_price_none_for_executor_to_stamp():
    """The strategist never sets ``opened_price`` — that is the executor's job.

    Rationale: at strategist-time the order has not yet been submitted to the
    broker, so any "open price" we might pick (last-trade, midpoint, etc.)
    would be a guess that diverges from the real fill price the executor
    later observes.  Worse, when the open is for a *new* ticker not yet in
    ``current_prices``, the old derivation silently fell back to ``0.0``,
    which then propagated into persistence and crashed the next tick's
    held-view renderer with a divide-by-zero.

    The architectural fix splits responsibility cleanly:
      - strategist emits intent (target_price, stop_price, horizon, rationale)
      - executor stamps the fact (opened_price) post-fill

    This test pins the strategist side of that contract.
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
    ctx = _ctx(weights={})
    out = derive_legacy_fields([stance], ctx)

    assert out.new_positions["AAPL"].opened_price is None


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
        horizon="swing",
        target_price=240.0,
        stop_price=190.0,
    )
    # Current weight 0.08 → preferred 0.15; difference 0.07 > SIZE_CHANGE_EPSILON (0.02)
    # → derive_lifecycle_action returns "add"
    ctx = _ctx(weights={"AAPL": 0.08})
    out = derive_legacy_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.15}
    assert out.new_positions == {}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}
