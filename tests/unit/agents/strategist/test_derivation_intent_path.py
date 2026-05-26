"""iter-3 derivation path tests — three-verb contract (buy / sell / update).

NOTE: The pre-iter-3 test classes (TestTargetWeightsReadIntentPath,
TestCloseReasonFromIntent, TestTrimReasonFromIntent, TestHeldCoverageInvariantPreserved)
that exercised the old open / close / trim / hold verb set were deleted in
the iter-3 sweep — those verbs no longer exist in the schema.

The coverage they provided is replaced by the equivalent three-verb tests
below, plus the tests in ``test_derivation.py`` (iter-3 section).
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

    def test_buy_stance_not_in_sell_reasons(self):
        """A buy stance must not add an entry to sell_reasons."""
        stances = [TickerStance(ticker="AVGO", intent="buy", weight=0.04, rationale="ok")]
        ctx = _ctx(watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert "AVGO" not in result.sell_reasons


# ---------------------------------------------------------------------------
# Sell path — sell_reasons populated from stance.reason
# ---------------------------------------------------------------------------


class TestSellReasonFromIntent:
    """sell_reasons must be populated from stance.reason on a sell stance."""

    def test_sell_with_reason_populates_sell_reasons(self):
        """A full sell with a reason must populate sell_reasons[ticker]."""
        stances = [TickerStance(
            ticker="AVGO", intent="sell", reason="guidance cut invalidates thesis",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.sell_reasons["AVGO"] == "guidance cut invalidates thesis"
        assert result.target_weights["AVGO"] == 0.0

    def test_partial_sell_reduces_weight(self):
        """A partial sell (sell + weight) reduces current weight by the stated delta."""
        stances = [TickerStance(
            ticker="AVGO", intent="sell", weight=0.02,
            reason="taking partial profits at 50% to target",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == pytest.approx(0.03)
        assert result.sell_reasons["AVGO"] == "taking partial profits at 50% to target"


# ---------------------------------------------------------------------------
# Update path — weight is carried forward unchanged
# ---------------------------------------------------------------------------


class TestUpdateCarryForward:
    """update stances must leave target_weights unchanged (weight carried forward)."""

    def test_update_carries_weight_forward(self):
        """An update stance must not alter the ticker's weight."""
        stances = [TickerStance(
            ticker="AVGO", intent="update", reason="raising my Q4 revenue estimate",
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.05
        assert "AVGO" not in result.sell_reasons


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
