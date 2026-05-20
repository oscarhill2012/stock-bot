"""TickerStance schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance
from config.strategist import get_strategist_config


def test_minimal_valid_zero_weight():
    """A zero-weight stance (hold-flat / full close) needs only the
    base fields — lifecycle hint fields stay optional in that case
    because there is no capital committed.
    """
    s = TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.7, rationale="hold")
    assert s.ticker == "AAPL"
    assert s.preferred_weight == 0.0
    assert s.horizon is None


def test_minimal_valid_nonzero_weight_requires_lifecycle_hints():
    """A non-zero stance must carry horizon / target_price / stop_price
    — see ``TickerStance._require_lifecycle_hints_on_nonzero``.
    """
    s = TickerStance(
        ticker="AAPL",
        preferred_weight=0.05,
        conviction=0.7,
        rationale="open",
        horizon="swing",
        target_price=210.0,
        stop_price=185.0,
    )
    assert s.preferred_weight == 0.05
    assert s.horizon == "swing"


def test_rejects_nonzero_weight_without_lifecycle_hints():
    """Any non-zero stance missing ``horizon`` / ``target_price`` /
    ``stop_price`` must fail at schema-validation time so the
    ``output_schema`` parse aborts the tick loudly rather than
    silently producing a partial decision.
    """
    with pytest.raises(ValidationError) as excinfo:
        TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.7, rationale="open")
    msg = str(excinfo.value)
    assert "horizon" in msg
    assert "target_price" in msg
    assert "stop_price" in msg


def test_open_with_full_lifecycle_fields():
    s = TickerStance(
        ticker="AAPL", preferred_weight=0.08, conviction=0.7,
        rationale="FCF + insider", horizon="swing",
        target_price=210.0, stop_price=185.0, catalyst="Q3",
    )
    assert s.horizon == "swing"
    assert s.target_price == 210.0


def test_close_with_close_reason():
    s = TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                     rationale="exit", close_reason="thesis broken")
    assert s.close_reason == "thesis broken"


def test_trim_with_trim_reason():
    """A trim stance still holds capital — so the lifecycle hint fields
    are required alongside ``trim_reason``.
    """
    s = TickerStance(
        ticker="AAPL",
        preferred_weight=0.03,
        conviction=0.5,
        rationale="reduce",
        horizon="swing",
        target_price=220.0,
        stop_price=180.0,
        trim_reason="profit-taking",
    )
    assert s.trim_reason == "profit-taking"


def test_rejects_preferred_weight_out_of_range():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=1.5, conviction=0.5, rationale="x")


def test_rejects_conviction_out_of_range():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.5, conviction=1.5, rationale="x")


def test_rejects_rationale_over_schema_cap():
    """``rationale`` is bounded by the *schema* cap (prompt cap +
    ``slack_percent`` headroom — see the "two-tier convention" note in
    ``src/config/strategist.py``).  Reads the live schema cap so that
    retuning either the prompt cap or the slack does not silently
    invalidate this regression.
    """

    cfg        = get_strategist_config()
    schema_cap = cfg.schema_cap(cfg.stance_caps.rationale_max_chars)

    with pytest.raises(ValidationError):
        # Use a zero-weight stance here so the test isolates the
        # rationale-length check — a non-zero weight would also trip
        # the lifecycle-hint validator and muddy the failure mode.
        TickerStance(
            ticker="AAPL",
            preferred_weight=0.0,
            conviction=0.5,
            rationale="x" * (schema_cap + 1),  # one char over the *schema* (slack-applied) cap
        )


def test_rejects_unknown_horizon():
    """``horizon`` is a ``Literal`` so unknown values must fail field
    validation.  Use a zero-weight stance to isolate the check from
    the lifecycle-hint validator (which would also trip on a
    non-zero weight that omits target_price / stop_price).
    """
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5,
                     rationale="x", horizon="forever")


def test_round_trip():
    original = TickerStance(
        ticker="MSFT", preferred_weight=0.06, conviction=0.6,
        rationale="cloud tailwind", horizon="long_term",
        target_price=450.0, stop_price=395.0,
    )
    rebuilt = TickerStance.model_validate(original.model_dump(mode="json"))
    assert rebuilt == original


# --- Boundary value tests ---

def test_preferred_weight_boundary_zero():
    """Weight of exactly 0.0 (full close) is a valid boundary value."""
    s = TickerStance(ticker="AAPL", preferred_weight=0.0, conviction=0.5, rationale="exit")
    assert s.preferred_weight == 0.0


def test_preferred_weight_boundary_one():
    """Weight of exactly 1.0 (full concentration) is a valid boundary value.

    Carries the lifecycle hint fields because a non-zero weight is
    committing capital — see ``_require_lifecycle_hints_on_nonzero``.
    """
    s = TickerStance(
        ticker="AAPL",
        preferred_weight=1.0,
        conviction=0.5,
        rationale="all-in",
        horizon="swing",
        target_price=300.0,
        stop_price=180.0,
    )
    assert s.preferred_weight == 1.0


def test_conviction_boundary_values():
    """Conviction at both ends of [0.0, 1.0] should be accepted.

    Both stances carry the lifecycle hint fields since
    ``preferred_weight > 0`` — the boundary under test is the
    ``conviction`` field, not lifecycle discipline.
    """
    low = TickerStance(
        ticker="AAPL",
        preferred_weight=0.05,
        conviction=0.0,
        rationale="no idea",
        horizon="swing",
        target_price=220.0,
        stop_price=180.0,
    )
    high = TickerStance(
        ticker="AAPL",
        preferred_weight=0.05,
        conviction=1.0,
        rationale="certain",
        horizon="swing",
        target_price=220.0,
        stop_price=180.0,
    )
    assert low.conviction == 0.0
    assert high.conviction == 1.0
