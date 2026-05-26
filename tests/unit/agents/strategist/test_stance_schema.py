"""TickerStance schema tests — Tier 1, no LLM.

Tests are organised by validator / rule:

- iter-3 three-verb canonical form: buy / sell / update
- ``TestIntentRequired`` confirms intent is non-optional.
- ``TestLegacyFieldRejection`` confirms deleted fields are not silently
  accepted (``extra="forbid"`` on the model config).

NOTE: The pre-iter-3 ``TestValidStances``, ``TestRequireIntentFields``, and
``TestBoundaryValues`` classes that exercised the old verb set
(open / add / trim / close / hold) were deleted in the iter-3 sweep — those
verbs are now rejected by the schema.  The equivalent coverage for the
three-verb schema lives in the test functions below.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance
from config.strategist import get_strategist_config


# ---------------------------------------------------------------------------
# intent is non-optional — required field
# ---------------------------------------------------------------------------

class TestIntentRequired:
    """intent is non-optional; omitting it must raise."""

    def test_no_intent_raises(self):
        """TickerStance without intent raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(ticker="X")

    def test_intent_none_raises(self):
        """Explicitly passing intent=None also raises."""
        with pytest.raises((ValidationError, TypeError)):
            # Pydantic v2 may raise TypeError on a Literal field receiving None.
            TickerStance(ticker="X", intent=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Legacy field rejection (extra="forbid" + field deletion)
# ---------------------------------------------------------------------------

class TestLegacyFieldRejection:
    """Deleted fields must be rejected by the schema, not silently ignored.

    ``extra="forbid"`` on the ModelConfig guarantees that any caller
    still passing the old kwargs gets a loud ValidationError rather than
    a silently truncated stance.
    """

    def test_legacy_preferred_weight_kwarg_rejected(self):
        """preferred_weight no longer exists — passing it raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="buy",
                weight=0.05, preferred_weight=0.05,
                rationale="ok",
            )

    def test_legacy_conviction_kwarg_rejected(self):
        """conviction no longer exists — passing it raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="buy",
                weight=0.05, conviction=0.8,
                rationale="ok",
            )

    def test_legacy_close_reason_kwarg_rejected(self):
        """close_reason no longer exists — sell uses rationale instead."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="sell",
                rationale="exit", close_reason="exit",
            )

    def test_legacy_trim_reason_kwarg_rejected(self):
        """trim_reason no longer exists — sell (partial) uses rationale instead."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="sell",
                weight=0.03, rationale="partial exit",
                trim_reason="partial exit",
            )

    def test_legacy_reason_kwarg_rejected(self):
        """``reason`` was collapsed into ``rationale`` — passing it must raise."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="sell",
                reason="legacy field — should be rejected as extra",
            )


# ---------------------------------------------------------------------------
# Boundary value tests — buy-specific constraints
# ---------------------------------------------------------------------------

class TestBoundaryValues:
    """Confirm field-level constraints on the three-verb schema."""

    def test_weight_boundary_one_on_buy_raises(self):
        """weight=1.0 on buy exceeds the per-trade delta cap (0.05) — must raise."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="AAPL", intent="buy",
                weight=1.0, rationale="all-in",
            )

    def test_accepts_long_rationale_no_schema_cap(self):
        """``rationale`` has no Pydantic schema-level ``max_length``.

        The prompt states the upper bound in words; the schema cap was removed
        in iter-3 to prevent Vertex's constrained decoder from padding rationale
        strings toward the cap.  This test pins that decision.
        """
        cfg        = get_strategist_config()
        schema_cap = cfg.schema_cap(cfg.stance_caps.rationale_max_chars)

        # Should accept a rationale comfortably over the prior schema cap.
        s = TickerStance(
            ticker="AAPL", intent="buy",
            weight=0.03,
            rationale="x" * (schema_cap + 1),
        )

        assert len(s.rationale or "") == schema_cap + 1

    def test_round_trip_serialisation(self):
        """A valid stance survives a JSON round-trip via model_dump / model_validate."""
        original = TickerStance(
            ticker="MSFT", intent="buy",
            weight=0.04, rationale="cloud tailwind",
        )
        rebuilt = TickerStance.model_validate(original.model_dump(mode="json"))
        assert rebuilt == original


# ---------------------------------------------------------------------------
# iter-3 schema rewrite — three-verb canonical form
# ---------------------------------------------------------------------------

def test_buy_requires_ticker_weight_rationale():
    """buy stance requires ticker, weight in (0, max_delta_per_buy], and rationale.

    No horizon, target_price, or stop_price required (or accepted)
    on a buy stance — those fields are removed from the new schema.  The
    over-cap weight is sourced from ``config.risk_gate`` so the test stays
    in sync with the single source of truth for the buy ceiling.
    """
    from agents.strategist.stance_schema import TickerStance
    from config.risk_gate import get_risk_gate_config

    cap = get_risk_gate_config().max_delta_per_buy
    in_band = cap / 2.0                         # comfortably within the band
    over_cap = cap + 0.01                       # any weight above the configured cap

    s = TickerStance(ticker="AAPL", intent="buy", weight=in_band, rationale="iPhone launch catalyst")
    assert s.intent == "buy"
    assert s.weight == in_band

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="rationale"):
        TickerStance(ticker="AAPL", intent="buy", weight=in_band)

    with pytest.raises(ValidationError, match="weight"):
        TickerStance(ticker="AAPL", intent="buy", weight=over_cap, rationale="x")

    with pytest.raises(ValidationError, match="target_price|extra"):
        TickerStance(ticker="AAPL", intent="buy", weight=in_band, rationale="x", target_price=250.0)


def test_sell_full_close_when_weight_absent():
    """sell stance with no weight is a full close.  Rationale required."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(ticker="AAPL", intent="sell", rationale="thesis invalidated")
    assert s.intent == "sell"
    assert s.weight is None

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="rationale"):
        TickerStance(ticker="AAPL", intent="sell")


def test_sell_partial_with_weight_in_unit_interval():
    """sell with weight is a partial trim.  Weight must be in (0, 1.0]."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(ticker="AAPL", intent="sell", weight=0.03, rationale="taking partial profit")
    assert s.weight == 0.03

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", intent="sell", weight=1.5, rationale="x")


def test_update_prose_only():
    """update stance carries only a rationale — no weight, no catalyst."""
    from agents.strategist.stance_schema import TickerStance

    s = TickerStance(ticker="AAPL", intent="update", rationale="revising the AI catalyst timeline downward")
    assert s.intent == "update"

    import pytest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", intent="update", weight=0.03, rationale="x")


def test_old_verbs_rejected_with_clear_message():
    """open / add / trim / close / hold all fail with a migration hint."""
    from agents.strategist.stance_schema import TickerStance
    import pytest
    from pydantic import ValidationError

    for old in ("open", "add", "trim", "close", "hold"):
        with pytest.raises(ValidationError) as exc:
            TickerStance(ticker="AAPL", intent=old)
        assert "buy" in str(exc.value) and "sell" in str(exc.value)


def test_sell_rejects_catalyst():
    """catalyst is buy-only; sell must reject it (mirrors rationale rejection)."""
    from agents.strategist.stance_schema import TickerStance
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="catalyst"):
        TickerStance(ticker="AAPL", intent="sell", rationale="x", catalyst="earnings beat")
