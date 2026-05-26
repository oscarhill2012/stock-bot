"""derive_decision_fields tests — Tier 1, no LLM.

Covers the main derivation paths:
  - open stance → target_weights
  - close stance → target_weights (0.0) + close_reasons
  - trim stance  → target_weights + trim_reasons
  - hold stance  → target_weights only
  - multiple stances → correct aggregation
  - held ticker omitted → StrategistContractViolation
  - carry-forward does not override emitted stances
  - add action only populates target_weight
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    weights: dict[str, float] | None = None,
    watchlist: list[str] | None = None,
) -> TickContext:
    """Build a minimal TickContext for test use.

    Parameters
    ----------
    weights:
        Optional mapping of ticker → current portfolio weight.
    watchlist:
        Optional full watchlist for this tick.  Defaults to an empty list,
        which disables the carry-forward padding pass — keeping
        single-stance tests focused on the per-stance dispatch logic.

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
        current_weights=weights or {},
        watchlist=watchlist or [],
    )


def test_open_creates_target_weight():
    """An open stance (intent="open", weight > 0) must set target_weights.

    Derivation only concerns itself with ``target_weights``,
    ``close_reasons``, and ``trim_reasons``.
    """
    stance = TickerStance(
        ticker="AAPL",
        intent="open",
        weight=0.08,
        rationale="open",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
    )
    ctx = _ctx(weights={})
    out = derive_decision_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.08}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_close_records_close_reason():
    """A close stance (intent="close") should record the reason in close_reasons."""
    stance = TickerStance(
        ticker="AAPL",
        intent="close",
        reason="thesis broken",
    )
    ctx = _ctx(weights={"AAPL": 0.08})
    out = derive_decision_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.0}
    assert out.close_reasons == {"AAPL": "thesis broken"}
    assert out.trim_reasons == {}


def test_trim_records_trim_reason():
    """A trim stance (intent="trim") should record the reason in trim_reasons.

    Trim stances carry weight, horizon, target_price, stop_price to support
    ongoing exit discipline — the position still holds capital.
    """
    stance = TickerStance(
        ticker="MSFT",
        intent="trim",
        weight=0.05,
        reason="lock in profits",
    )
    ctx = _ctx(weights={"MSFT": 0.12})
    out = derive_decision_fields([stance], ctx)

    assert out.target_weights == {"MSFT": 0.05}
    assert out.trim_reasons == {"MSFT": "lock in profits"}
    assert out.close_reasons == {}


def test_hold_yields_only_target_weight():
    """A hold stance (intent="hold") should only contribute to target_weights.

    hold/update do not carry weight; the current weight is carried forward
    as 0.0 by the ``or 0.0`` fallback in derivation.
    """
    stance = TickerStance(
        ticker="MSFT",
        intent="hold",
        reason="thesis intact",
    )
    ctx = _ctx(weights={"MSFT": 0.06})
    out = derive_decision_fields([stance], ctx)

    assert out.target_weights == {"MSFT": 0.0}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_multiple_stances_aggregate_correctly():
    """Multiple stances are each dispatched to the correct output bucket."""
    stances = [
        TickerStance(
            ticker="AAPL",
            intent="open",
            weight=0.08,
            rationale="open",
            horizon="swing",
            target_price=210.0,
            stop_price=185.0,
        ),
        TickerStance(
            ticker="MSFT",
            intent="close",
            reason="rotate",
        ),
        TickerStance(
            ticker="NVDA",
            intent="trim",
            weight=0.05,
            reason="overweight",
        ),
    ]
    ctx = _ctx(weights={"MSFT": 0.10, "NVDA": 0.15})
    out = derive_decision_fields(stances, ctx)

    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.0, "NVDA": 0.05}
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {"NVDA": "overweight"}


def test_open_stance_goes_into_target_weights_not_close_or_trim():
    """An open stance must only populate target_weights — not close_reasons or trim_reasons."""
    stance = TickerStance(
        ticker="AAPL",
        intent="open",
        weight=0.08,
        rationale="open",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
    )
    ctx = _ctx(weights={})
    out = derive_decision_fields([stance], ctx)

    # target_weights is the only populated field — no close_reasons, no trim_reasons.
    assert out.target_weights == {"AAPL": 0.08}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_held_ticker_without_stance_raises_contract_violation():
    """An omitted held ticker now raises StrategistContractViolation (Spec B / D3).

    Pre-Spec-B, the carry-forward block silently padded any held ticker the
    strategist did not emit a stance for.  Spec B removes that: every pre-tick
    held ticker MUST be explicitly touched by a stance.  Omission of a held
    ticker is now a hard contract violation.

    Flat tickers (TSLA) remain optional — the active-stances model survives
    for them (Spec B §'Active-stances model').
    """
    # One explicit close stance for MSFT (held); AAPL is also held but has
    # NO stance — this is the scenario that must now raise.
    stances = [
        TickerStance(
            ticker="MSFT",
            intent="close",
            reason="rotate",
        ),
    ]
    ctx = _ctx(
        weights={"AAPL": 0.08, "MSFT": 0.10},  # AAPL held (no stance), MSFT held (close stance)
        watchlist=["AAPL", "MSFT", "TSLA"],
    )

    with pytest.raises(StrategistContractViolation) as excinfo:
        derive_decision_fields(stances, ctx)

    # The error message must name the uncovered held ticker.
    assert "AAPL" in str(excinfo.value)
    assert "Held position(s)" in str(excinfo.value)


def test_carry_forward_does_not_override_emitted_stances():
    """When a ticker IS emitted, the strategist's weight wins over the carry-forward default.

    Guards against a Pass-2 bug where the padding loop could clobber a freshly
    set ``target_weights`` entry if the order-of-operations were inverted.
    """
    stance = TickerStance(
        ticker="AAPL",
        intent="add",
        weight=0.15,
    )
    ctx = _ctx(
        weights={"AAPL": 0.08},
        watchlist=["AAPL"],
    )
    out = derive_decision_fields([stance], ctx)

    # Emitted weight (0.15) — NOT the current weight (0.08) — must survive.
    assert out.target_weights == {"AAPL": 0.15}


def test_add_action_only_populates_target_weight():
    """An add stance (intent="add", weight > current) should only populate target_weights.

    The 'add' action adds to an existing position; it does not open a fresh one,
    so PositionThesis is not created here (that was created on the original open).
    Derivation delegates PositionThesis assembly entirely to the executor.
    """
    stance = TickerStance(
        ticker="AAPL",
        intent="add",
        weight=0.15,
    )
    # Current weight 0.08 → preferred 0.15; difference 0.07 > SIZE_CHANGE_EPSILON (0.02)
    ctx = _ctx(weights={"AAPL": 0.08})
    out = derive_decision_fields([stance], ctx)

    assert out.target_weights == {"AAPL": 0.15}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


# ── Three-verb schema tests (iter-3 Task 4) ───────────────────────────────────
# These tests exercise the rewritten Pass 1 dispatch for the canonical
# buy / sell / update vocabulary.  The TickContext constructor is used in
# its simplified form (no tick_id / decision_tag / now required) to keep
# the tests focused on dispatch logic.


def test_derivation_dispatches_buy_to_target_weight():
    """A buy stance writes the delta into target_weights additively."""
    from agents.strategist.derivation import derive_decision_fields
    from agents.strategist.stance_schema import TickerStance
    from agents.strategist.derivation import TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers=set(),
        current_weights={"AAPL": 0.0, "MSFT": 0.0},
    )
    stances = [TickerStance(ticker="AAPL", intent="buy", weight=0.03, rationale="iPhone launch catalyst")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.03
    assert "AAPL" not in derived.sell_reasons


def test_derivation_dispatches_sell_full_close():
    """A sell stance with no weight is a full close — target_weight = 0."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="sell", reason="thesis invalidated")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.0
    assert derived.sell_reasons["AAPL"] == "thesis invalidated"


def test_derivation_dispatches_sell_partial():
    """A sell stance with weight=0.03 reduces current weight by 0.03."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="sell", weight=0.03, reason="trimming on overbought")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.05
    assert derived.sell_reasons["AAPL"] == "trimming on overbought"


def test_derivation_update_does_not_change_weight():
    """An update stance carries forward the current weight unchanged."""
    from agents.strategist.derivation import derive_decision_fields, TickContext
    from agents.strategist.stance_schema import TickerStance

    ctx = TickContext(watchlist=["AAPL"], held_tickers={"AAPL"}, current_weights={"AAPL": 0.08})
    stances = [TickerStance(ticker="AAPL", intent="update", reason="revising AI catalyst timeline downward but still holding")]
    derived = derive_decision_fields(stances, ctx)
    assert derived.target_weights["AAPL"] == 0.08
    assert "AAPL" not in derived.sell_reasons


def test_derivation_held_omission_carries_weight_forward():
    """A held ticker with no stance keeps its current weight (implicit hold)."""
    from agents.strategist.derivation import derive_decision_fields, TickContext

    ctx = TickContext(
        watchlist=["AAPL", "MSFT"],
        held_tickers={"AAPL", "MSFT"},
        current_weights={"AAPL": 0.05, "MSFT": 0.07},
    )
    derived = derive_decision_fields([], ctx)
    assert derived.target_weights["AAPL"] == 0.05
    assert derived.target_weights["MSFT"] == 0.07
