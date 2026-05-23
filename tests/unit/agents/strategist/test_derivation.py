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
    watchlist: list[str] | None = None,
) -> TickContext:
    """Build a minimal TickContext for test use.

    Parameters
    ----------
    weights:
        Optional mapping of ticker → current portfolio weight.
    watchlist:
        Optional full watchlist for this tick.  Defaults to an empty list,
        which disables the carry-forward padding pass — keeping legacy
        single-stance tests focused on the per-stance dispatch logic.

    Returns
    -------
    TickContext
        A TickContext seeded with ``tick_id="tick_X"``, ``decision_tag="x"``,
        and a fixed datetime of 2026-05-08 14:00 UTC.

    Notes
    -----
    ``new_positions`` was removed from ``DerivedFields`` in Band 6.  The
    executor now assembles the ``PositionThesis`` from the fill price + stance
    via ``apply_stance_to_thesis``; the strategist never had an honest fill
    price to pre-compute it.
    """
    return TickContext(
        tick_id="tick_X",
        decision_tag="x",
        now=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        current_weights=weights or {},
        watchlist=watchlist or [],
    )


def test_open_creates_target_weight():
    """An open stance (current weight 0, preferred > 0) must set target_weights.

    Band 6: derivation no longer creates a ``PositionThesis`` for open stances.
    The executor assembles it from the fill price + stance via
    ``apply_stance_to_thesis``.  Derivation only concerns itself with
    ``target_weights``, ``close_reasons``, and ``trim_reasons``.
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

    assert out.target_weights == {"AAPL": 0.08}
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

    # Band 6: derivation no longer produces new_positions; the executor
    # assembles PositionThesis from the fill price + stance itself.
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {"NVDA": "overweight"}


def test_open_stance_goes_into_target_weights_not_close_or_trim():
    """An open stance must only populate target_weights — not close_reasons or trim_reasons.

    Band 6: ``new_positions`` is no longer assembled by derivation; the executor
    handles that.  We confirm the output shape does not accidentally bleed open
    stances into the wrong buckets.
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

    # target_weights is the only populated field — no close_reasons, no trim_reasons.
    assert out.target_weights == {"AAPL": 0.08}
    assert out.close_reasons == {}
    assert out.trim_reasons == {}


def test_carry_forward_pads_unemitted_watchlist_tickers():
    """Watchlist tickers the strategist did NOT emit a stance for keep their current weight.

    This pins the "active-stances only" contract: the strategist emits a stance
    only when it wants to *change* a ticker's exposure (open / add / trim /
    close).  Omission is read as an *implicit hold* — held positions stay held
    at their current weight, flat tickers stay flat.

    Derivation pads ``target_weights`` for every watchlist ticker so downstream
    (risk_gate, executor) keeps seeing an exhaustive dict; no other field
    (``close_reasons`` / ``trim_reasons``) is touched on a carry-forward,
    because no lifecycle action is happening.
    """
    # One explicit close stance; AAPL (held) and TSLA (flat) are NOT in stances.
    stances = [
        TickerStance(
            ticker="MSFT",
            preferred_weight=0.0,
            conviction=0.6,
            rationale="exit",
            close_reason="rotate",
        ),
    ]
    ctx = _ctx(
        weights={"AAPL": 0.08, "MSFT": 0.10},          # AAPL held, MSFT held, TSLA flat
        watchlist=["AAPL", "MSFT", "TSLA"],
    )
    out = derive_legacy_fields(stances, ctx)

    # MSFT closed explicitly, AAPL carried forward, TSLA padded at flat (0.0).
    assert out.target_weights == {"AAPL": 0.08, "MSFT": 0.0, "TSLA": 0.0}

    # The carry-forward pass must NOT invent close-reasons or trim-reasons.
    assert out.close_reasons == {"MSFT": "rotate"}
    assert out.trim_reasons == {}


def test_carry_forward_does_not_override_emitted_stances():
    """When a ticker IS emitted, the strategist's preferred weight wins over the carry-forward default.

    Guards against a Pass-2 bug where the padding loop could clobber a freshly
    set ``target_weights`` entry if the order-of-operations were inverted.
    """
    stance = TickerStance(
        ticker="AAPL",
        preferred_weight=0.15,
        conviction=0.8,
        rationale="add",
        horizon="swing",
        target_price=240.0,
        stop_price=190.0,
    )
    ctx = _ctx(
        weights={"AAPL": 0.08},
        watchlist=["AAPL"],
    )
    out = derive_legacy_fields([stance], ctx)

    # Emitted preferred_weight (0.15) — NOT the current weight (0.08) — must survive.
    assert out.target_weights == {"AAPL": 0.15}


def test_add_action_only_populates_target_weight():
    """An add stance (preferred_weight > current by >δ but both above ε) should only
    populate target_weights — no close_reasons, no trim_reasons.

    The 'add' action adds to an existing position; it doesn't open a fresh one,
    so PositionThesis is not created here (that was created on the original open).
    Band 6: derivation delegates PositionThesis assembly entirely to the executor.
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
    assert out.close_reasons == {}
    assert out.trim_reasons == {}
