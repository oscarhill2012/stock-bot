"""Tier-1 tests for derive_smart_money_verdict.

These tests exercise the deterministic verdict heuristic in isolation.
They are pure-function tests: no I/O, no mocks, no ADK state.
"""
from __future__ import annotations

from agents.analysts.heuristics import SmartMoneyHeuristics
from contract.extractors.smart_money import derive_smart_money_verdict


def _h() -> SmartMoneyHeuristics:
    """Return a standard SmartMoneyHeuristics instance for test use."""
    return SmartMoneyHeuristics(
        multi_filer_min_count=3,
        high_activity_trade_count=5,
        lone_filer_confidence_floor=0.1,
        consensus_confidence_ceiling=0.9,
        magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    """Return a zeroed feature dict with optional overrides.

    Mirrors the ``_KEYS`` tuple in ``contract.extractors.smart_money``.
    """
    base = {
        "n_politicians": 0.0,
        "n_buys_30d": 0.0,
        "n_sells_30d": 0.0,
        "total_dollar_value_buys": 0.0,
        "total_dollar_value_sells": 0.0,
        "net_flow_dollar": 0.0,
        "is_no_data": 0.0,
    }
    base.update(overrides)
    return base


def test_no_data_returns_neutral_no_data():
    """is_no_data flag yields the no-data verdict."""
    v = derive_smart_money_verdict(_features(is_no_data=1.0), _h())
    assert v.is_no_data is True
    assert v.lean == "neutral"


def test_net_buying_leans_bullish():
    """Positive net flow yields a bullish lean."""
    v = derive_smart_money_verdict(
        _features(
            net_flow_dollar=50_000,
            total_dollar_value_buys=60_000,
            total_dollar_value_sells=10_000,
            n_politicians=2,
            n_buys_30d=3,
        ),
        _h(),
    )
    assert v.lean == "bullish"


def test_net_selling_leans_bearish():
    """Negative net flow yields a bearish lean."""
    v = derive_smart_money_verdict(
        _features(
            net_flow_dollar=-50_000,
            total_dollar_value_buys=10_000,
            total_dollar_value_sells=60_000,
            n_politicians=2,
            n_sells_30d=3,
        ),
        _h(),
    )
    assert v.lean == "bearish"


def test_lone_filer_confidence_floor():
    """One filer + one trade is capped at lone_filer_confidence_floor."""
    v = derive_smart_money_verdict(
        _features(n_politicians=1, n_buys_30d=1, net_flow_dollar=1_000, total_dollar_value_buys=1_000),
        _h(),
    )
    # Generous slack above the literal floor — this is a ceiling assertion.
    assert v.confidence <= 0.2


def test_consensus_ceiling_when_many_filers_high_activity():
    """Many filers + high activity raises confidence near the ceiling (>= 0.85).

    The spec ceiling is 0.9; with 5 politicians and 6 buys in 30 days the
    confidence should be well above the midpoint — a loose >= 0.5 assertion
    would pass even a broken implementation.  The tighter bound catches
    regressions in the multi-filer consensus path.
    """
    v = derive_smart_money_verdict(
        _features(
            n_politicians=5,
            n_buys_30d=6,
            net_flow_dollar=50_000,
            total_dollar_value_buys=60_000,
            total_dollar_value_sells=10_000,
        ),
        _h(),
    )
    assert v.confidence >= 0.85


def test_magnitude_uses_flow_asymmetry_not_absolute_dollars():
    """magnitude scales by flow ratio, not raw dollar amounts.

    Two trades with the same buy/sell ratio (9:1) but different absolute
    amounts should produce nearly identical magnitudes.
    """
    small = derive_smart_money_verdict(
        _features(
            net_flow_dollar=900,
            total_dollar_value_buys=1_000,
            total_dollar_value_sells=100,
            n_politicians=2,
            n_buys_30d=2,
        ),
        _h(),
    )
    big = derive_smart_money_verdict(
        _features(
            net_flow_dollar=9_000,
            total_dollar_value_buys=10_000,
            total_dollar_value_sells=1_000,
            n_politicians=2,
            n_buys_30d=2,
        ),
        _h(),
    )
    # Same flow asymmetry → similar magnitudes (within 5 % tolerance).
    assert abs(small.magnitude - big.magnitude) < 0.05


def test_closed_vocabulary():
    """key_factors stays inside the closed smart-money vocabulary."""
    allowed = {
        "net_buying",
        "net_selling",
        "multi_filer_consensus",
        "lone_filer",
        "high_volume_flow",
        "mixed_activity",
    }
    v = derive_smart_money_verdict(
        _features(
            net_flow_dollar=50_000,
            total_dollar_value_buys=60_000,
            total_dollar_value_sells=10_000,
            n_politicians=5,
            n_buys_30d=6,
        ),
        _h(),
    )
    for tag in v.key_factors:
        assert tag in allowed
