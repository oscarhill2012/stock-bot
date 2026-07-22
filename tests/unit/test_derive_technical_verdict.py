"""Table-driven tests for the trend/momentum composite technical verdict."""
from __future__ import annotations

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _tech(**overrides) -> TechnicalHeuristics:
    base = dict(
        trend_weight=0.50, anchor_52w_weight=0.25, rel_strength_weight=0.25,
        composite_neutral_band=0.10, horizon_days=60,
        vol_regime_window=60, vol_regime_extreme_z=1.5,
        vol_ratio_breakout=1.3, vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0, magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )
    base.update(overrides)
    return TechnicalHeuristics(**base)


def _feats(**overrides) -> dict:
    # Non-no-data baseline (dodges the no-data fingerprint).
    feats = {"rsi_14": 55.0, "atr_pct_14": 2.0, "pct_change_5d": 0.0, "pct_change_20d": 0.01}
    feats.update(overrides)
    return feats


def test_all_three_agree_bullish():
    """Above MA200 + near 52w high + positive rel-strength → high-confidence bullish."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03),
        _tech(),
    )
    assert v.lean == "bullish"
    assert "trend_follow_up" in v.key_factors
    assert "anchor_52w_high" in v.key_factors
    assert "rel_strength_confirm" in v.key_factors
    assert v.confidence >= 0.8            # 3/3 agreement


def test_all_three_agree_bearish():
    """Below MA200 + near 52w low + negative rel-strength → bearish."""
    v = derive_technical_verdict(
        _feats(trend_state=-0.08, ma200_state=-1.0, dist_from_low_52w_pct=1.0,
               relative_strength_vs_spy_20d=-0.03),
        _tech(),
    )
    assert v.lean == "bearish"
    assert "trend_follow_down" in v.key_factors


def test_split_vote_inside_band_is_neutral():
    """Trend up but rel-strength down and no 52w anchor → score inside band → neutral.

    Note: with the shared default weights (trend=0.50, rel_strength=0.25) this
    exact vote pattern (+1/0/-1) nets a score of 0.25 — outside a 0.10 band and
    identical to the score in ``test_trend_dominates_by_weight`` (same votes,
    same weights).  A neutral-band test needs a band wide enough to swallow
    that score without contradicting the sibling "dominates" test, so this
    test widens ``composite_neutral_band`` for its own scenario rather than
    reusing the shared default.
    """
    v = derive_technical_verdict(
        _feats(trend_state=0.02, ma200_state=1.0, relative_strength_vs_spy_20d=-0.03),
        _tech(composite_neutral_band=0.30),
    )
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0


def test_trend_dominates_by_weight():
    """Trend (0.50) outvotes a lone opposing rel-strength (0.25) → follows trend."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=-0.01),
        _tech(),
    )
    assert v.lean == "bullish"


def test_magnitude_capped():
    """Magnitude never exceeds magnitude_cap."""
    v = derive_technical_verdict(
        _feats(trend_state=0.9, ma200_state=1.0, dist_from_high_52w_pct=-0.5,
               relative_strength_vs_spy_20d=0.5),
        _tech(magnitude_cap=0.6),
    )
    assert v.magnitude <= 0.6


def test_vol_regime_damps_confidence_not_lean():
    """A stressed vol regime lowers confidence but never flips the lean."""
    calm = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03, vol_regime_z=0.0),
        _tech(),
    )
    stressed = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, dist_from_high_52w_pct=-1.0,
               relative_strength_vs_spy_20d=0.03, vol_regime_z=3.0),
        _tech(),
    )
    assert stressed.lean == calm.lean == "bullish"
    assert stressed.confidence < calm.confidence
    assert "vol_regime_extreme" in stressed.key_factors


def test_horizon_days_from_config():
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=0.03),
        _tech(horizon_days=40),
    )
    assert v.horizon_days == 40


def test_no_data_fingerprint_still_fires():
    v = derive_technical_verdict(
        {"rsi_14": 0.0, "atr_pct_14": 0.0, "pct_change_5d": 0.0}, _tech(),
    )
    assert v.is_no_data is True


def test_reversal_tags_are_gone():
    """The retired reversal vocabulary must never appear."""
    v = derive_technical_verdict(
        _feats(trend_state=0.08, ma200_state=1.0, relative_strength_vs_spy_20d=0.03),
        _tech(),
    )
    for dead in ("reversal_up_fade", "reversal_down_bounce", "reversal_neutral"):
        assert dead not in v.key_factors
