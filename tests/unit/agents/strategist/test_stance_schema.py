"""TickerStance schema tests — Tier 1, no LLM.

Tests are organised by validator / rule:

- Top-level "valid minimal" tests confirm the happy path for each intent verb.
- ``_RequireIntentFields`` tests cover the verb-conditional field rules from
  the End-state contract table in ``spec-b-plan-3-strategist-minimisation.md``.
- ``_LegacyFieldRejection`` tests confirm that the deleted fields are not
  silently accepted (``extra="forbid"`` on the model config).
- Boundary value tests confirm field-level constraints (rationale length, etc.).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance
from config.strategist import get_strategist_config

# ---------------------------------------------------------------------------
# Happy-path tests — one per intent verb
# ---------------------------------------------------------------------------

class TestValidStances:
    """Confirm the happy path for each intent verb in the End-state contract."""

    def test_open_all_required_fields(self):
        """intent='open' with all required fields is valid."""
        s = TickerStance(
            ticker="AAPL",
            intent="open",
            weight=0.05,
            rationale="FCF + insider buying",
            horizon="swing",
            target_price=210.0,
            stop_price=185.0,
        )
        assert s.ticker == "AAPL"
        assert s.intent == "open"
        assert s.weight == 0.05
        assert s.horizon == "swing"

    def test_open_with_optional_catalyst(self):
        """Catalyst is optional on open — including it must succeed."""
        s = TickerStance(
            ticker="MSFT",
            intent="open",
            weight=0.06,
            rationale="cloud momentum",
            horizon="long_term",
            target_price=450.0,
            stop_price=395.0,
            catalyst="Azure revenue beat expected",
        )
        assert s.catalyst == "Azure revenue beat expected"

    def test_add_weight_required(self):
        """intent='add' with weight is valid; reason and other fields optional."""
        s = TickerStance(ticker="AAPL", intent="add", weight=0.08)
        assert s.intent == "add"
        assert s.weight == 0.08

    def test_trim_weight_and_reason_required(self):
        """intent='trim' with weight and reason is valid."""
        s = TickerStance(
            ticker="AAPL",
            intent="trim",
            weight=0.03,
            reason="taking partial profits at 50% of target",
        )
        assert s.intent == "trim"
        assert s.weight == 0.03

    def test_close_reason_required(self):
        """intent='close' with reason is valid; weight must be absent."""
        s = TickerStance(ticker="AAPL", intent="close", reason="thesis invalidated")
        assert s.intent == "close"
        assert s.weight is None

    def test_hold_reason_required(self):
        """intent='hold' with reason is valid; weight must be absent."""
        s = TickerStance(ticker="AAPL", intent="hold", reason="waiting for earnings")
        assert s.intent == "hold"

    def test_update_reason_and_one_field(self):
        """intent='update' with reason and at least one thesis field is valid."""
        s = TickerStance(
            ticker="AAPL",
            intent="update",
            reason="revised target after guidance",
            target_price=220.0,
        )
        assert s.intent == "update"
        assert s.target_price == 220.0


# ---------------------------------------------------------------------------
# _require_intent_fields — verb-conditional validation rules
# ---------------------------------------------------------------------------

class TestRequireIntentFields:
    """Verb-conditional field requirements from the End-state contract table."""

    # ── open ──────────────────────────────────────────────────────────────

    def test_open_missing_weight_raises(self):
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(
                ticker="AAPL", intent="open",
                rationale="ok", horizon="swing",
                target_price=200.0, stop_price=180.0,
            )

    def test_open_missing_rationale_raises(self):
        with pytest.raises(ValidationError, match="rationale"):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.05, horizon="swing",
                target_price=200.0, stop_price=180.0,
            )

    def test_open_missing_horizon_raises(self):
        with pytest.raises(ValidationError, match="horizon"):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.05, rationale="ok",
                target_price=200.0, stop_price=180.0,
            )

    def test_open_missing_target_price_raises(self):
        with pytest.raises(ValidationError, match="target_price"):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.05, rationale="ok",
                horizon="swing", stop_price=180.0,
            )

    def test_open_missing_stop_price_raises(self):
        with pytest.raises(ValidationError, match="stop_price"):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.05, rationale="ok",
                horizon="swing", target_price=200.0,
            )

    # ── add ───────────────────────────────────────────────────────────────

    def test_add_missing_weight_raises(self):
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(ticker="AAPL", intent="add")

    def test_add_with_optional_fields_accepted(self):
        """Horizon/target/stop/catalyst are optional on add."""
        s = TickerStance(
            ticker="AAPL", intent="add", weight=0.07,
            reason="momentum accelerating",
            horizon="swing",
        )
        assert s.weight == 0.07

    # ── trim ──────────────────────────────────────────────────────────────

    def test_trim_missing_weight_raises(self):
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(ticker="AAPL", intent="trim", reason="profit-taking")

    def test_trim_missing_reason_raises(self):
        with pytest.raises(ValidationError, match="reason"):
            TickerStance(ticker="AAPL", intent="trim", weight=0.03)

    # ── close ─────────────────────────────────────────────────────────────

    def test_close_missing_reason_raises(self):
        """reason is required on close — silent closes are forbidden."""
        with pytest.raises(ValidationError, match="reason"):
            TickerStance(ticker="AAPL", intent="close")

    def test_close_with_weight_raises(self):
        """weight is forbidden on close — use 'trim' for a partial exit.

        Rationale: close means full exit; weight=0.0 redundantly carries the
        same meaning but creates ambiguity about whether 0.0 means 'close
        completely' or 'hold flat'.  We forbid it outright to remove the choice.
        See Plan 3 'Out of scope' footnote on weight=0.0 semantics.
        """
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(ticker="AAPL", intent="close", weight=0.0, reason="exit")

    # ── hold ──────────────────────────────────────────────────────────────

    def test_hold_missing_reason_raises(self):
        with pytest.raises(ValidationError, match="reason"):
            TickerStance(ticker="AAPL", intent="hold")

    def test_hold_with_weight_raises(self):
        """weight is forbidden on hold — a hold carries no size change."""
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(ticker="AAPL", intent="hold", weight=0.05, reason="waiting")

    # ── update ────────────────────────────────────────────────────────────

    def test_update_missing_reason_raises(self):
        with pytest.raises(ValidationError, match="reason"):
            TickerStance(ticker="AAPL", intent="update", target_price=220.0)

    def test_update_missing_all_thesis_fields_coerces_to_hold(self, caplog):
        """update with reason but no thesis fields salvages to hold + WARN.

        Empirically (Sep 2025 baseline backtest) Vertex Gemini occasionally
        emits ``intent='update'`` while writing prose like *"Updating target
        to reflect the new catalyst"* yet never populates ``target_price`` /
        ``stop_price`` / ``horizon`` / ``catalyst``.  The structural shape
        is identical to a valid ``hold`` (reason present, no commitment
        fields, no weight) and the executor would do nothing either way,
        so the validator coerces rather than aborting the tick.

        The WARN log is the loud part of the otherwise-quiet salvage: a
        spike in the rate signals that the prompt or verb set needs work.
        """
        import logging
        with caplog.at_level(logging.WARNING, logger="agents.strategist.stance_schema"):
            s = TickerStance(
                ticker = "AAPL",
                intent = "update",
                reason = "Updating target to reflect the new acquisition catalyst.",
            )

        # Intent has been silently rewritten to ``hold`` — downstream code
        # sees a clean hold stance, no special-casing required.
        assert s.intent == "hold"
        assert s.ticker == "AAPL"
        assert s.reason == "Updating target to reflect the new acquisition catalyst."

        # The salvage MUST be observable in logs — silent coercion would
        # mask a real prompt/model failure mode if the rate ever spiked.
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stance_update_coerced_to_hold" in r.getMessage() for r in warn_records), (
            f"Expected WARN with 'stance_update_coerced_to_hold'; got: {[r.getMessage() for r in warn_records]}"
        )
        assert any("ticker=AAPL" in r.getMessage() for r in warn_records)

    def test_update_missing_all_thesis_fields_and_reason_coerces_to_hold(self, caplog):
        """A structurally-empty update (no thesis fields, no reason, no weight)
        is still coerced to ``hold`` — the executor would do nothing either
        way, and the prior strict-raise behaviour was aborting JNJ ticks in
        the 2025-09 baseline backtest with no actionable downstream effect.

        The salvage synthesises a placeholder reason so the downstream hold
        shape (which requires reason ≠ None) stays valid, and emits the WARN
        so a spike in this rate is observable.
        """
        import logging
        with caplog.at_level(logging.WARNING, logger="agents.strategist.stance_schema"):
            s = TickerStance(ticker="AAPL", intent="update")

        # Coerced to hold — downstream code sees a clean hold stance.
        assert s.intent == "hold"
        assert s.ticker == "AAPL"

        # A synthetic reason was filled in so the hold shape is valid.
        assert s.reason is not None
        assert "coerced" in s.reason.lower()

        # The salvage MUST be observable in logs.
        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("stance_update_coerced_to_hold" in r.getMessage() for r in warn_records), (
            f"Expected WARN with 'stance_update_coerced_to_hold'; got: {[r.getMessage() for r in warn_records]}"
        )

    def test_update_with_weight_raises(self):
        """weight is forbidden on update — no trade occurs."""
        with pytest.raises(ValidationError, match="weight"):
            TickerStance(
                ticker="AAPL", intent="update",
                weight=0.05, reason="revised thesis",
                target_price=220.0,
            )

    def test_update_each_thesis_field_individually_accepted(self):
        """Any single thesis field suffices for update alongside reason."""
        for field, value in (
            ("target_price", 220.0),
            ("stop_price",   180.0),
            ("horizon",      "long_term"),
            ("catalyst",     "next earnings"),
        ):
            s = TickerStance(
                ticker="AAPL", intent="update",
                reason="thesis updated", **{field: value},
            )
            assert getattr(s, field) == value


# ---------------------------------------------------------------------------
# intent is non-optional (Band 3 — required field)
# ---------------------------------------------------------------------------

class TestIntentRequired:
    """intent is non-optional after Band 3; omitting it must raise."""

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
                ticker="X", intent="open",
                weight=0.05, preferred_weight=0.05,
                rationale="ok", horizon="swing",
                target_price=200.0, stop_price=180.0,
            )

    def test_legacy_conviction_kwarg_rejected(self):
        """conviction no longer exists — passing it raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="open",
                weight=0.05, conviction=0.8,
                rationale="ok", horizon="swing",
                target_price=200.0, stop_price=180.0,
            )

    def test_legacy_close_reason_kwarg_rejected(self):
        """close_reason no longer exists — passing it raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="close",
                reason="exit", close_reason="exit",
            )

    def test_legacy_trim_reason_kwarg_rejected(self):
        """trim_reason no longer exists — passing it raises ValidationError."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="X", intent="trim",
                weight=0.03, reason="partial exit",
                trim_reason="partial exit",
            )


# ---------------------------------------------------------------------------
# Boundary value tests — field-level constraints
# ---------------------------------------------------------------------------

class TestBoundaryValues:
    """Confirm field-level constraints (ranges, lengths, literal sets)."""

    def test_weight_boundary_zero_on_open_raises(self):
        """weight=0.0 on open is not meaningful — a non-zero weight is required."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.0, rationale="ok",
                horizon="swing", target_price=200.0, stop_price=180.0,
            )

    def test_weight_boundary_one_on_open(self):
        """weight=1.0 (full concentration) is technically valid at schema level."""
        s = TickerStance(
            ticker="AAPL", intent="open",
            weight=1.0, rationale="all-in",
            horizon="swing", target_price=300.0, stop_price=180.0,
        )
        assert s.weight == 1.0

    def test_accepts_long_rationale_no_schema_cap(self):
        """``rationale`` is no longer bounded by the Pydantic schema cap.

        After the 2026-05-24 two-class split + field reorder
        (commit 7590ba1), ``TickerStance.rationale`` has no ``max_length``.
        Vertex's constrained decoder treats schema ``maxLength`` as a fill
        target and pads strings toward the cap; the prompt states the
        upper bound in words instead.  This test pins that decision so a
        future "tidy-up" doesn't silently restore the constraint and
        re-introduce the fill-bias spiral.
        """
        cfg        = get_strategist_config()
        schema_cap = cfg.schema_cap(cfg.stance_caps.rationale_max_chars)

        # Should accept a rationale comfortably over the prior schema cap.
        s = TickerStance(
            ticker="AAPL", intent="open",
            weight=0.05,
            rationale="x" * (schema_cap + 1),
            horizon="swing",
            target_price=200.0,
            stop_price=180.0,
        )

        assert len(s.rationale or "") == schema_cap + 1

    def test_rejects_unknown_horizon(self):
        """horizon is a Literal — unknown values must fail field validation."""
        with pytest.raises(ValidationError):
            TickerStance(
                ticker="AAPL", intent="open",
                weight=0.05, rationale="ok",
                horizon="forever",  # type: ignore[arg-type]
                target_price=200.0, stop_price=180.0,
            )

    def test_round_trip_serialisation(self):
        """A valid stance survives a JSON round-trip via model_dump / model_validate."""
        original = TickerStance(
            ticker="MSFT", intent="open",
            weight=0.06, rationale="cloud tailwind",
            horizon="long_term",
            target_price=450.0, stop_price=395.0,
        )
        rebuilt = TickerStance.model_validate(original.model_dump(mode="json"))
        assert rebuilt == original
