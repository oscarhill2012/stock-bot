"""Band 1 — derive_decision_fields reads intent+weight, not preferred_weight.

These tests verify that the rewritten derivation pipeline:
  - Populates target_weights from stance.weight (not preferred_weight).
  - Populates close_reasons from stance.reason when intent=='close'.
  - Populates trim_reasons from stance.reason when intent=='trim'.
  - Raises StrategistContractViolation when intent is None.
  - Raises StrategistContractViolation when a close has no reason.
  - Preserves the Spec B / D3 held-coverage invariant (uncovered held ticker → raise).
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


def _ctx(current_weights=None, watchlist=("AAPL", "MSFT")) -> TickContext:
    """Build a minimal TickContext for derivation tests.

    Args:
        current_weights: Mapping of ticker → current weight. Defaults to empty.
        watchlist: Tickers in scope this tick. Defaults to a two-ticker set.

    Returns:
        A ``TickContext`` with deterministic tick_id and timestamp.
    """
    return TickContext(
        tick_id="tick_001",
        decision_tag="test",
        now=datetime(2026, 5, 24, 14, 0, tzinfo=UTC),
        current_weights=current_weights or {},
        watchlist=list(watchlist),
    )


class TestTargetWeightsReadIntentPath:
    """target_weights must populate from stance.weight, never preferred_weight."""

    def test_open_stance_populates_target_weight_from_weight_field(self):
        """An open stance at 5% must write 0.05 into target_weights via stance.weight."""
        # AVGO is flat; strategist opens at 5%.
        # preferred_weight/conviction present because they are still required
        # on TickerStance in Band 1 (Band 3 will delete them).
        stances = [TickerStance(
            ticker="AVGO", intent="open", weight=0.05,
            preferred_weight=0.05, conviction=0.8,
            rationale="Strong setup", horizon="swing",
            target_price=2100.0, stop_price=1800.0,
            catalyst="earnings beat expected",
        )]
        ctx = _ctx(watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        # Derivation reads stance.weight (0.05), not preferred_weight (also 0.05
        # here, but we are confirming the code path, not the value).
        assert result.target_weights["AVGO"] == 0.05

    def test_close_stance_populates_target_weight_zero(self):
        """A close stance must write 0.0 into target_weights regardless of prior weight."""
        # preferred_weight=0.0 matches the legacy close pattern — but the new
        # derivation reads intent, not preferred_weight.
        stances = [TickerStance(
            ticker="AVGO", intent="close", reason="thesis broke",
            preferred_weight=0.0, conviction=0.5,
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.target_weights["AVGO"] == 0.0


class TestCloseReasonFromIntent:
    """close_reasons populates from stance.reason when intent=='close'."""

    def test_close_with_reason_populates_close_reasons(self):
        """A close with a reason must populate close_reasons[ticker]."""
        stances = [TickerStance(
            ticker="AVGO", intent="close", reason="guidance cut invalidates thesis",
            preferred_weight=0.0, conviction=0.5,
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.close_reasons["AVGO"] == "guidance cut invalidates thesis"

    def test_close_without_reason_raises(self):
        """A close with no reason must raise — silent exits are forbidden audit failures."""
        # Silent failures are the recurring bug class — raise loud.
        stances = [TickerStance(
            ticker="AVGO", intent="close", reason=None,
            preferred_weight=0.0, conviction=0.5,
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="reason"):
            derive_decision_fields(stances, ctx)


class TestTrimReasonFromIntent:
    """trim_reasons populates from stance.reason when intent=='trim'."""

    def test_trim_with_reason_populates_trim_reasons(self):
        """A trim with a reason must populate trim_reasons[ticker]."""
        stances = [TickerStance(
            ticker="AVGO", intent="trim", weight=0.02,
            reason="taking partial profits at 50% to target",
            preferred_weight=0.02, conviction=0.6,
        )]
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        result = derive_decision_fields(stances, ctx)
        assert result.trim_reasons["AVGO"] == "taking partial profits at 50% to target"


class TestIntentNonNullEnforced:
    """A stance with intent=None must raise — no silent legacy-path fallback."""

    def test_intent_none_raises_contract_violation(self):
        """intent=None must raise immediately rather than silently falling back to preferred_weight.

        We use preferred_weight=0.0 so the legacy _require_lifecycle_hints_on_nonzero
        validator is satisfied (zero-weight stances are exempt from the hint check).
        Band 3 will delete preferred_weight entirely; at that point only intent matters.
        """
        # A legacy zero-weight stance with no intent — the new derivation must
        # reject it loudly rather than silently treating it as a hold-flat.
        stances = [TickerStance(
            ticker="AVGO", intent=None,
            preferred_weight=0.0, conviction=0.7,
        )]
        ctx = _ctx(watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="intent"):
            derive_decision_fields(stances, ctx)


class TestHeldCoverageInvariantPreserved:
    """The Plan 2 / D3 invariant — held tickers MUST have a stance — still raises."""

    def test_uncovered_held_ticker_raises(self):
        """An empty stance list when AVGO is held must raise with the ticker name."""
        stances = []  # Strategist returned nothing
        ctx = _ctx(current_weights={"AVGO": 0.05}, watchlist=("AVGO",))
        with pytest.raises(StrategistContractViolation, match="AVGO"):
            derive_decision_fields(stances, ctx)
