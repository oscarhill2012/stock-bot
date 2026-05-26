"""iter-3 derivation edge-case tests.

iter-3 removed the Spec-B / D3 "error on omission" rule — held tickers
with no matching stance no longer raise ``StrategistContractViolation``;
they simply carry their current weight forward via Pass 2.

The two tests that remain cover edge cases still relevant after that
removal:

  * Dust position (sub-``ORDER_EPSILON``) is treated as flat, NOT held,
    so its omission is legal and derivation must not raise.
  * Flat watchlist ticker with no stance is always legal — the
    active-stances model requires stances only for deliberate trades.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.derivation import (
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(
    *,
    current_weights: dict[str, float],
    watchlist:       list[str],
) -> TickContext:
    """Build a TickContext fixture with sensible defaults.

    Parameters
    ----------
    current_weights:
        Mapping of ticker → current portfolio weight (pre-tick).
    watchlist:
        Full watchlist of tracked tickers for this tick.

    Returns
    -------
    TickContext
        A TickContext seeded with fixed tick metadata for deterministic tests.
    """

    return TickContext(
        tick_id          = "tick_005",
        decision_tag     = "afternoon_sweep",
        now              = datetime(2026, 5, 5, 14, 0, tzinfo=UTC),
        current_weights  = current_weights,
        watchlist        = watchlist,
    )



def test_dust_position_below_order_epsilon_is_treated_as_flat() -> None:
    """A position whose weight is below ``ORDER_EPSILON`` is *flat*, not held.

    Regression test for the 2026-05-25 backtest abort on AMD: the broker
    fully closed AMD but left a 3.55e-15 share residue, producing a
    sub-epsilon positive weight.  Strict ``> 0.0`` classified AMD as
    held, while the shim's thesis-registry view (``user:positions``)
    already considered it closed and produced a mode header of "4 held
    positions" rather than 5.  The LLM emitted four stances; derivation
    then raised ``StrategistContractViolation`` for the missing AMD
    stance.

    The fix aligns derivation with the shim by filtering out any
    position whose weight is below the canonical ``ORDER_EPSILON``
    threshold (1e-6) — the same threshold the rest of derivation
    already uses for prior/new-zero arithmetic.

    See also ``docs/todo-fixes.md`` §5.3 — the deeper question is
    whether weight-based portfolio maths is the right primitive at all
    when share-level dust can accumulate this kind of floating-point
    rounding.
    """

    # No stance for AMD — the LLM has no reason to emit one because the
    # shim's "4 held positions" header excluded it.  Derivation must
    # agree (dust is operationally flat) rather than reject.
    stances = [
        TickerStance(
            intent = "update",
            ticker = "AVGO",
            reason = "Position intact; no new evidence.",
        ),
    ]

    derived = derive_decision_fields(
        stances,
        _ctx(
            current_weights = {
                "AVGO": 0.05,
                # Sub-epsilon dust quantity — the exact value observed in
                # the 2026-05-25 backtest probe was 3.55e-15 shares; the
                # corresponding weight is well below ORDER_EPSILON (1e-6).
                "AMD":  1e-12,
            },
            watchlist       = ["AVGO", "AMD", "MSFT"],
        ),
    )

    # Derivation must succeed (no StrategistContractViolation raised).
    # AMD's sub-epsilon dust weight carries forward verbatim (1e-12) — it is
    # below ORDER_EPSILON so it is not classified as held.  The critical
    # invariant is that derivation does NOT raise here; the residual dust
    # value is functionally zero and the risk gate / executor will ignore it.
    assert derived.target_weights["AMD"]  < 1e-6   # dust — below order epsilon
    # AVGO at 0.05 with an ``update`` stance carries its current weight forward.
    assert derived.target_weights["AVGO"] == pytest.approx(0.05)


def test_flat_ticker_without_stance_is_ok() -> None:
    """Omitting a flat watchlist ticker is the active-stances model — legal.

    AVGO is held and has a stance; MSFT and XOM are flat and have no
    stance.  Derivation must succeed and pad target_weights for the
    flat tickers with their current weight (0.0).
    """

    stances = [
        TickerStance(
            # AVGO is held — the strategist reviews it and decides to hold:
            # no trade, thesis unchanged.  ``intent="update"`` with
            # ``reason`` satisfies the verb-conditional validator.
            intent = "update",
            ticker = "AVGO",
            reason = "No new evidence; commitments unchanged.",
        ),
    ]

    derived = derive_decision_fields(
        stances,
        _ctx(
            current_weights = {"AVGO": 0.05},
            watchlist       = ["AVGO", "MSFT", "XOM"],
        ),
    )

    # Held ticker with ``update`` stance — weight carries forward verbatim.
    # ``update`` means "no trade; thesis unchanged"; the pre-tick weight is
    # preserved in target_weights so the risk gate / executor take no action.
    assert derived.target_weights["AVGO"] == pytest.approx(0.05)
    # Flat tickers — carry-forward pads to 0.0 (their current weight is 0.0).
    assert derived.target_weights["MSFT"] == 0.0
    assert derived.target_weights["XOM"]  == 0.0
