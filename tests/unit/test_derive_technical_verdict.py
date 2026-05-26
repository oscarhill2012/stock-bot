"""Tier-1 tests for derive_technical_verdict — table-driven cases per spec.

Note on 52-week distance keys:
    The extractor produces ``dist_from_high_52w_pct`` (negative value —
    price / high - 1.0, so -3.0 means 3 % below the 52w high) and
    ``dist_from_low_52w_pct`` (positive — price / low - 1.0).  "Near" is
    determined by ``abs(dist_from_high_52w_pct) <= h.near_52w_extreme_pct``
    and ``dist_from_low_52w_pct <= h.near_52w_extreme_pct``.
"""
from __future__ import annotations

import pytest  # noqa: F401

from agents.analysts.heuristics import TechnicalHeuristics
from contract.extractors.technical import derive_technical_verdict


def _h() -> TechnicalHeuristics:
    """Canonical fixture heuristics — matches the shapes used by the spec examples."""
    return TechnicalHeuristics(
        rsi_overbought=75,
        rsi_oversold=25,
        pct_change_momentum_scale=4.0,
        vol_ratio_breakout=1.5,
        vol_ratio_dry_up=0.7,
        atr_high_volatility_pct=5.0,
        near_52w_extreme_pct=5.0,
        confidence_base=0.5,
        confidence_boost_step=0.2,
        confidence_penalty_step=0.3,
        magnitude_cap=1.0,
    )


def _features(**overrides) -> dict:
    """Build a minimal valid feature dict, allowing key overrides.

    Defaults represent a neutral, data-present state:
    - RSI in mid-range (50)
    - flat short and medium-term momentum
    - normal volume ratio (1.0)
    - moderate volatility (2 %)
    - moderately distant from both 52w extremes
      (dist_from_high is negative — 10 % below high;
       dist_from_low is positive — 30 % above low)
    """
    base = {
        "rsi_14": 50.0,
        "pct_change_5d": 0.0,
        "pct_change_20d": 0.0,
        "vol_ratio_20d": 1.0,
        "atr_pct_14": 2.0,
        # negative: price is 10 % below the 52w high
        "dist_from_high_52w_pct": -10.0,
        # positive: price is 30 % above the 52w low
        "dist_from_low_52w_pct": 30.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# No-data path
# ---------------------------------------------------------------------------

def test_no_data_path():
    """All-zero core features ⇒ is_no_data flag set and lean is neutral."""
    v = derive_technical_verdict(
        _features(rsi_14=0, pct_change_20d=0, atr_pct_14=0),
        _h(),
    )
    assert v.is_no_data is True
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0


# ---------------------------------------------------------------------------
# Lean from momentum
# ---------------------------------------------------------------------------

def test_uptrend_20d():
    """Positive 20-day momentum leans bullish."""
    v = derive_technical_verdict(
        _features(pct_change_20d=0.08, pct_change_5d=0.03),
        _h(),
    )
    assert v.lean == "bullish"


def test_downtrend_20d():
    """Negative 20-day momentum leans bearish."""
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=-0.03),
        _h(),
    )
    assert v.lean == "bearish"


# ---------------------------------------------------------------------------
# RSI flip logic
# ---------------------------------------------------------------------------

def test_overbought_with_positive_momentum_keeps_trend_lean():
    """RSI overbought + positive 5d momentum on an uptrend must NOT flip to bearish.

    Regression cover for Bug #12 (baseline-window-2025-09-iter-2.md §Bug #12):
    the prior "exhaustion" flip fought trending names (GOOGL/UNH/AMD ran 12-30 %
    while the verdict reported bearish).  Persistent overbought RSI is a feature
    of strong trends, not an exit signal — the lean must reflect the 20d trend
    score rather than being unconditionally flipped.
    """
    v = derive_technical_verdict(
        _features(rsi_14=80, pct_change_5d=0.04, pct_change_20d=0.05),
        _h(),
    )

    # Trend score is positive, so lean should remain bullish.
    assert v.lean == "bullish"

    # The overbought factor is still informational — keep it in the rationale.
    assert "rsi_overbought" in v.key_factors


def test_overbought_factor_emitted_regardless_of_lean():
    """``rsi_overbought`` must appear in ``key_factors`` whenever RSI > threshold.

    Even though we no longer flip the lean on this signal alone, the factor
    itself remains valuable context for downstream consumers (rationale text,
    strategist prompt).
    """
    v = derive_technical_verdict(
        _features(rsi_14=80, pct_change_5d=0.04, pct_change_20d=0.05),
        _h(),
    )
    assert "rsi_overbought" in v.key_factors


def test_oversold_capitulation_flips_to_bullish():
    """RSI below oversold threshold AND negative 5d momentum ⇒ bullish flip."""
    v = derive_technical_verdict(
        _features(rsi_14=20, pct_change_5d=-0.04, pct_change_20d=-0.05),
        _h(),
    )
    assert v.lean == "bullish"


# ---------------------------------------------------------------------------
# Volume effects on magnitude
# ---------------------------------------------------------------------------

def test_vol_ratio_nan_emits_neither_factor():
    """Bug #14: NaN ``vol_ratio_20d`` (insufficient history) emits no volume factor.

    Previously, the extractor defaulted ``vol_ratio_20d`` to 0.0 on short
    windows, which compared less than ``h.vol_ratio_dry_up`` (0.7) and
    spuriously appended ``vol_dry_up``.  With a NaN sentinel, neither
    ``vol_breakout`` nor ``vol_dry_up`` should appear.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.05, vol_ratio_20d=float("nan")),
        _h(),
    )
    assert "vol_dry_up" not in v.key_factors
    assert "vol_breakout" not in v.key_factors


def test_vol_ratio_real_dry_up_still_emits_factor():
    """A genuinely low ``vol_ratio_20d`` (< 0.7) still triggers ``vol_dry_up``.

    Regression cover: the NaN guard must NOT short-circuit real low-volume
    signals.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.05, vol_ratio_20d=0.4),
        _h(),
    )
    assert "vol_dry_up" in v.key_factors


def test_vol_breakout_boosts_magnitude():
    """High volume ratio above breakout threshold lifts magnitude."""
    quiet = derive_technical_verdict(
        _features(pct_change_20d=0.08, vol_ratio_20d=1.0),
        _h(),
    )
    boom = derive_technical_verdict(
        _features(pct_change_20d=0.08, vol_ratio_20d=2.0),
        _h(),
    )
    assert boom.magnitude > quiet.magnitude


# ---------------------------------------------------------------------------
# Confidence modifiers
# ---------------------------------------------------------------------------

def test_momentum_agree_boosts_confidence():
    """5d and 20d momentum aligned (same sign) lifts confidence vs divergence."""
    agree = derive_technical_verdict(
        _features(pct_change_5d=0.03, pct_change_20d=0.08),
        _h(),
    )
    disagree = derive_technical_verdict(
        _features(pct_change_5d=-0.03, pct_change_20d=0.08),
        _h(),
    )
    assert agree.confidence > disagree.confidence


def test_near_52w_high_boosts_confidence():
    """Within near_52w_extreme_pct of 52-week high boosts confidence.

    dist_from_high_52w_pct is negative — -2.0 means 2 % below the high
    (within the 5 % threshold), while -20.0 means 20 % below (outside).
    """
    far = derive_technical_verdict(
        _features(pct_change_20d=0.08, dist_from_high_52w_pct=-20.0),
        _h(),
    )
    near = derive_technical_verdict(
        _features(pct_change_20d=0.08, dist_from_high_52w_pct=-2.0),
        _h(),
    )
    assert near.confidence > far.confidence


def test_high_atr_penalises_confidence():
    """ATR percentage above the volatility threshold drops confidence."""
    calm = derive_technical_verdict(
        _features(pct_change_20d=0.08, atr_pct_14=2.0),
        _h(),
    )
    choppy = derive_technical_verdict(
        _features(pct_change_20d=0.08, atr_pct_14=8.0),
        _h(),
    )
    assert choppy.confidence < calm.confidence


# ---------------------------------------------------------------------------
# Golden / death cross (Bug #13)
# ---------------------------------------------------------------------------
#
# The extractor emits ``golden_cross`` / ``death_cross`` whenever ratios are
# available, but the verdict layer previously ignored them. They now appear as
# corroborating factors in ``key_factors`` so the strategist can weigh the
# medium-term trend regime alongside the short-term RSI / momentum signals.
# Neither flag is allowed to flip ``lean`` on its own — that responsibility
# stays with 20-day momentum.
# ---------------------------------------------------------------------------

def test_golden_cross_emits_factor():
    """``golden_cross == 1.0`` ⇒ ``"golden_cross"`` appended to key_factors."""
    v = derive_technical_verdict(
        _features(pct_change_20d=0.05, golden_cross=1.0, death_cross=0.0),
        _h(),
    )
    assert "golden_cross" in v.key_factors


def test_death_cross_emits_factor():
    """``death_cross == 1.0`` ⇒ ``"death_cross"`` appended to key_factors."""
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.05, golden_cross=0.0, death_cross=1.0),
        _h(),
    )
    assert "death_cross" in v.key_factors


def test_no_cross_emits_neither_factor():
    """Both flags 0.0 ⇒ neither ``golden_cross`` nor ``death_cross`` in key_factors."""
    v = derive_technical_verdict(
        _features(pct_change_20d=0.02, golden_cross=0.0, death_cross=0.0),
        _h(),
    )
    assert "golden_cross" not in v.key_factors
    assert "death_cross" not in v.key_factors


def test_missing_cross_keys_do_not_blow_up():
    """Feature dict without the cross keys must not raise — mirrors live behaviour.

    The extractor omits ``golden_cross`` / ``death_cross`` entirely when
    ratios are absent. The verdict layer reads via ``.get(..., 0.0)`` so a
    missing key simply produces no factor.
    """
    feats = _features(pct_change_20d=0.05)
    feats.pop("golden_cross", None)
    feats.pop("death_cross", None)

    v = derive_technical_verdict(feats, _h())

    assert "golden_cross" not in v.key_factors
    assert "death_cross" not in v.key_factors


def test_golden_cross_does_not_flip_bearish_lean():
    """A bullish ``golden_cross`` factor must NOT override a bearish trend lean.

    The cross flag is corroborating context only — lean is owned by the 20d
    momentum + RSI capitulation logic.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.05, pct_change_5d=-0.01, golden_cross=1.0),
        _h(),
    )
    # 20d momentum is negative ⇒ lean stays bearish despite the golden_cross tag.
    assert v.lean == "bearish"
    assert "golden_cross" in v.key_factors


# ---------------------------------------------------------------------------
# Closed vocabulary
# ---------------------------------------------------------------------------

def test_closed_vocabulary():
    """Every key_factor emitted must belong to the closed technical vocabulary."""
    allowed = {
        "trend_up_20d", "trend_down_20d",
        "momentum_agree", "momentum_disagree",
        "rsi_overbought", "rsi_oversold",
        "near_52w_high", "near_52w_low",
        "vol_breakout", "vol_dry_up",
        "high_volatility",
        "golden_cross", "death_cross",
    }
    v = derive_technical_verdict(
        _features(
            pct_change_20d=0.08,
            pct_change_5d=0.03,
            vol_ratio_20d=2.0,
            # -2.0 means 2 % below high — within the 5 % threshold
            dist_from_high_52w_pct=-2.0,
            golden_cross=1.0,
        ),
        _h(),
    )
    for tag in v.key_factors:
        assert tag in allowed, f"out-of-vocabulary tag emitted: {tag!r}"
