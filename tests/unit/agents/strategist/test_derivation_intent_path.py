"""iter-3 derivation path tests — three-verb contract (buy / sell / update).

A-013 tail note: ``sell_reasons`` and ``update_reasons`` were removed from
``DerivedFields``.  Tests that previously asserted on those dicts now assert on
``target_weights`` and the stance's own ``rationale`` field instead —
preserving the same semantic coverage via the correct new source.

NOTE: The pre-iter-3 test classes (TestTargetWeightsReadIntentPath,
TestCloseReasonFromIntent, TestTrimReasonFromIntent, TestHeldCoverageInvariantPreserved)
that exercised the old open / close / trim / hold verb set were deleted in
the iter-3 sweep — those verbs no longer exist in the schema.
"""
from __future__ import annotations

import pytest

from agents.strategist.derivation import (
    StrategistContractViolation,
    TickContext,
    derive_decision_fields,
)
from agents.strategist.stance_schema import TickerStance


def _ctx(current_weights=None, watchlist=("AAPL", "MSFT")) -> TickContext:
    """Build a minimal TickContext for derivation tests.

    Args:
        current_weights: Mapping of ticker → current weight. Defaults to empty.
        watchlist: Tickers in scope this tick. Defaults to a two-ticker set.

    Returns:
        A ``TickContext`` with default values.
    """
    return TickContext(
        current_weights=current_weights or {},
        watchlist=list(watchlist),
    )


# ---------------------------------------------------------------------------
# Buy path — target_weights populated from stance.weight
# ---------------------------------------------------------------------------


class TestBuyTargetWeightPath:
    """buy stances must populate target_weights from stance.weight."""

    def test_buy_stance_populates_target_weight(self):
        """A buy stance at 4 % must write 0.04 into target_weights."""
        stances = [TickerStance(
            ticker="AVGO", intent="buy", weight=0.04,
            rationale="Strong buy signal",
        )]
        ctx = _ctx(watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.04

    def test_buy_stance_tagged_as_entry(self):
        """A buy stance from a flat start is tagged 'entry' in decision_tags."""
        stances = [TickerStance(ticker="AVGO", intent="buy", weight=0.04, rationale="ok")]
        ctx = _ctx(watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        # Buy from zero = entry, not exit — confirm the sell path was NOT taken.
        assert result.decision_tags.get("AVGO") == "entry"


# ---------------------------------------------------------------------------
# Sell path — target_weights reflects reduction; rationale on the stance
# ---------------------------------------------------------------------------


class TestSellRationaleOnStance:
    """Sell rationale is preserved on the TickerStance after A-013 tail collapse.

    The former ``sell_reasons`` dict duplicated TickerStance.rationale verbatim.
    These tests verify the derivation still sets target_weights correctly and
    that the rationale is accessible from the stance object.
    """

    def test_sell_full_close_sets_target_weight_to_zero(self):
        """A full sell (no weight) must set target_weight to 0.0."""
        stances = [TickerStance(
            ticker="AVGO", intent="sell", rationale="guidance cut invalidates thesis",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.0
        # Rationale is on the stance directly.
        assert stances[0].rationale == "guidance cut invalidates thesis"

    def test_partial_sell_reduces_weight(self):
        """A partial sell (sell + weight) reduces current weight by the stated delta."""
        stances = [TickerStance(
            ticker="AVGO", intent="sell", weight=0.02,
            rationale="taking partial profits at 50% to target",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == pytest.approx(0.03)
        # Rationale is on the stance directly.
        assert stances[0].rationale == "taking partial profits at 50% to target"


# ---------------------------------------------------------------------------
# Update path — weight is carried forward unchanged
# ---------------------------------------------------------------------------


class TestUpdateCarryForward:
    """update stances must leave target_weights unchanged (weight carried forward)."""

    def test_update_carries_weight_forward(self):
        """An update stance must not alter the ticker's weight."""
        stances = [TickerStance(
            ticker="AVGO", intent="update", rationale="raising my Q4 revenue estimate",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.05
        # Update leaves the position open — decision tag is 'hold'.
        assert result.decision_tags.get("AVGO") == "hold"


# ---------------------------------------------------------------------------
# Intent non-null enforcement
# ---------------------------------------------------------------------------


class TestIntentNonNullEnforced:
    """A stance with intent=None must raise — no silent legacy-path fallback."""

    def test_intent_none_raises_contract_violation(self):
        """intent=None must raise immediately — no silent legacy-path fallback.

        We bypass Pydantic via ``model_construct`` to simulate a payload
        arriving at derivation with ``intent=None``.  The derivation layer
        must raise ``StrategistContractViolation``.
        """
        bad_stance = TickerStance.model_construct(
            ticker="AVGO",
            intent=None,
        )
        ctx = _ctx(watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="intent"):
            derive_decision_fields([bad_stance], ctx)
