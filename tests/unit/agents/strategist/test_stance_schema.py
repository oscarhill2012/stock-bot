"""TickerStance schema tests — Tier 1, no LLM."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from agents.strategist.stance_schema import TickerStance
from config.strategist import get_strategist_config


def test_minimal_valid():
    s = TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.7, rationale="open")
    assert s.ticker == "AAPL"
    assert s.preferred_weight == 0.05
    assert s.horizon is None


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
    s = TickerStance(ticker="AAPL", preferred_weight=0.03, conviction=0.5,
                     rationale="reduce", trim_reason="profit-taking")
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
        TickerStance(
            ticker="AAPL",
            preferred_weight=0.5,
            conviction=0.5,
            rationale="x" * (schema_cap + 1),  # one char over the *schema* (slack-applied) cap
        )


def test_rejects_unknown_horizon():
    with pytest.raises(ValidationError):
        TickerStance(ticker="AAPL", preferred_weight=0.5, conviction=0.5,
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
    """Weight of exactly 1.0 (full concentration) is a valid boundary value."""
    s = TickerStance(ticker="AAPL", preferred_weight=1.0, conviction=0.5, rationale="all-in")
    assert s.preferred_weight == 1.0


def test_conviction_boundary_values():
    """Conviction at both ends of [0.0, 1.0] should be accepted."""
    low = TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=0.0, rationale="no idea")
    high = TickerStance(ticker="AAPL", preferred_weight=0.05, conviction=1.0, rationale="certain")
    assert low.conviction == 0.0
    assert high.conviction == 1.0
