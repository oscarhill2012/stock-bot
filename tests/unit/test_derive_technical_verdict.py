"""Table-driven tests for the rewritten reversal-based technical verdict."""
from __future__ import annotations

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _tech(**overrides) -> TechnicalHeuristics:
    """Build a TechnicalHeuristics with sane defaults, overridable per-test."""
    base = dict(
        reversal_neutral_band_pct=0.03,
        reversal_magnitude_scale=8.0,
        reversal_confidence_base=0.5,
        reversal_horizon_days=7,
        vol_regime_window=60,
        vol_regime_elevated_z=1.5,
        vol_ratio_breakout=1.3,
        vol_ratio_dry_up=0.7,
        near_52w_extreme_pct=5.0,
        magnitude_cap=1.0,
        beta_confidence_damping_enabled=False,
    )
    base.update(overrides)
    return TechnicalHeuristics(**base)


def _feats(**overrides) -> dict:
    """Minimal computable feature dict (dodges the no-data fingerprint)."""
    feats = {"rsi_14": 55.0, "atr_pct_14": 2.0, "pct_change_5d": 0.0, "pct_change_20d": 0.01}
    feats.update(overrides)
    return feats


def test_recent_up_move_fades_bearish():
    """A recent 5-day UP move is faded DOWN (contrarian)."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.08), _tech())
    assert v.lean == "bearish"
    assert "reversal_up_fade" in v.key_factors


def test_recent_down_move_bounces_bullish():
    """A recent 5-day DOWN move is faded UP (contrarian)."""
    v = derive_technical_verdict(_feats(pct_change_5d=-0.08), _tech())
    assert v.lean == "bullish"
    assert "reversal_down_bounce" in v.key_factors


def test_inside_band_is_neutral_zero_confidence():
    """A move inside the neutral band yields a neutral, zero-confidence verdict."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.01), _tech())
    assert v.lean == "neutral"
    assert v.confidence == 0.0
    assert v.magnitude == 0.0
    assert "reversal_neutral" in v.key_factors


def test_magnitude_scales_and_caps():
    """Magnitude = min(|pct5| * scale, cap)."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.05), _tech())
    assert abs(v.magnitude - 0.40) < 1e-9
    capped = derive_technical_verdict(_feats(pct_change_5d=0.50), _tech())
    assert capped.magnitude == 1.0


def test_confidence_ramps_from_base_to_one():
    """At the band edge confidence == base; at ≥2× band it saturates to 1.0."""
    edge = derive_technical_verdict(_feats(pct_change_5d=0.03), _tech())
    assert abs(edge.confidence - 0.5) < 1e-9
    far = derive_technical_verdict(_feats(pct_change_5d=0.06), _tech())
    assert abs(far.confidence - 1.0) < 1e-9


def test_horizon_days_from_config():
    """The reversal horizon is written onto the verdict."""
    v = derive_technical_verdict(_feats(pct_change_5d=0.08), _tech(reversal_horizon_days=9))
    assert v.horizon_days == 9


def test_vol_regime_and_trend_are_tags_not_lean():
    """Vol-regime and trend-state surface as tags and never flip the reversal lean."""
    v = derive_technical_verdict(
        _feats(pct_change_5d=0.08, vol_regime_z=2.5, trend_state=0.10),
        _tech(),
    )
    # Lean still driven ONLY by the reversal read.
    assert v.lean == "bearish"
    assert "vol_regime_elevated" in v.key_factors
    assert "trend_above_ma200" in v.key_factors


def test_no_data_fingerprint_still_fires():
    """All three core indicators absent/zero → canonical no-data verdict."""
    v = derive_technical_verdict({"rsi_14": 0.0, "atr_pct_14": 0.0, "pct_change_5d": 0.0}, _tech())
    assert v.is_no_data is True
