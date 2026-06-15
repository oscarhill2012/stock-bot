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


def _h(
    momentum_neutral_band_pct: float = 0.02,
    rsi_mean_reversion: float = 35.0,
) -> TechnicalHeuristics:
    """Canonical fixture heuristics — matches the shapes used by the spec examples.

    Parameters
    ----------
    momentum_neutral_band_pct:
        Conviction gate threshold in fractional-return units (e.g. 0.02 = ±2 %).
        Defaults to the provisional config value so tests exercise a realistic band.
        Pass ``0.0`` to disable the gate and test raw sign-based lean behaviour.
    rsi_mean_reversion:
        RSI level below which a bearish 20d-trend call is downgraded to neutral.
        Defaults to the config default of 35.  Pass ``0.0`` to disable the rule.
    """
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
        momentum_neutral_band_pct=momentum_neutral_band_pct,
        rsi_mean_reversion=rsi_mean_reversion,
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
    """No-price-data fingerprint ⇒ is_no_data flag set and lean is neutral.

    The fingerprint fires when:
    - ``rsi_14 == 0``
    - ``pct_change_20d`` is ABSENT from the dict (Bug #23c: key omitted = "not
      computable"; ``.get()`` returns ``None`` which triggers the branch)
    - ``atr_pct_14 == 0``
    """
    # Build features dict without pct_change_20d to simulate insufficient bars.
    feats = _features(rsi_14=0, atr_pct_14=0)
    feats.pop("pct_change_20d", None)  # omit key → .get() returns None → no-data branch

    v = derive_technical_verdict(feats, _h())
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

def test_vol_ratio_absent_emits_neither_factor():
    """Bug #20: absent ``vol_ratio_20d`` (insufficient history) emits no volume factor.

    Previously, the extractor defaulted ``vol_ratio_20d`` to 0.0 on short
    windows, which compared less than ``h.vol_ratio_dry_up`` (0.7) and
    spuriously appended ``vol_dry_up``.  Bug #20 changed the sentinel from
    NaN → absent key.  ``.get()`` returns ``None`` which suppresses the factor.
    """
    feats = _features(pct_change_20d=0.05)
    feats.pop("vol_ratio_20d", None)  # omit key → .get() returns None → no factor

    v = derive_technical_verdict(feats, _h())
    assert "vol_dry_up" not in v.key_factors
    assert "vol_breakout" not in v.key_factors


def test_vol_ratio_nan_also_emits_neither_factor():
    """Defensive: legacy NaN ``vol_ratio_20d`` also suppresses volume factors.

    A stray ``float('nan')`` from an older code path or test fixture must not
    produce a spurious ``vol_dry_up`` factor — the verdict layer has a defensive
    ``math.isnan`` guard alongside the primary ``is None`` check.
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
        "rsi_moderate_oversold",
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


# ---------------------------------------------------------------------------
# Momentum neutral-band conviction gate
# ---------------------------------------------------------------------------
#
# The gate fires when ``abs(pct_change_20d) < h.momentum_neutral_band_pct``,
# forcing ``lean="neutral"`` regardless of sign.  Units throughout are
# fractional returns (0.02 = 2 %).
# ---------------------------------------------------------------------------

def test_neutral_band_inside_band_yields_neutral():
    """A small positive return inside the band ⇒ lean neutral (abstention).

    pct_change_20d=+0.01 is well inside the ±0.02 band; the analyst must
    not commit to bullish even though the sign is positive.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.01, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.02),
    )
    assert v.lean == "neutral", (
        f"Expected neutral for pct_change_20d=0.01 inside ±0.02 band, got {v.lean!r}"
    )


def test_neutral_band_negative_inside_band_yields_neutral():
    """A small negative return inside the band ⇒ lean neutral (abstention).

    pct_change_20d=-0.015 is inside the ±0.02 band; the analyst must not
    commit to bearish.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.015, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.02),
    )
    assert v.lean == "neutral", (
        f"Expected neutral for pct_change_20d=-0.015 inside ±0.02 band, got {v.lean!r}"
    )


def test_neutral_band_outside_band_bullish():
    """A return clearly above the band still yields bullish.

    pct_change_20d=+0.05 is outside the ±0.02 band; normal sign-based lean applies.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.05, pct_change_5d=0.02),
        _h(momentum_neutral_band_pct=0.02),
    )
    assert v.lean == "bullish", (
        f"Expected bullish for pct_change_20d=0.05 outside ±0.02 band, got {v.lean!r}"
    )


def test_neutral_band_outside_band_bearish():
    """A return clearly below the (negated) band still yields bearish.

    pct_change_20d=-0.05 is outside the ±0.02 band; normal sign-based lean applies.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.05, pct_change_5d=-0.02),
        _h(momentum_neutral_band_pct=0.02),
    )
    assert v.lean == "bearish", (
        f"Expected bearish for pct_change_20d=-0.05 outside ±0.02 band, got {v.lean!r}"
    )


def test_neutral_band_exact_boundary_is_directional():
    """A return exactly at the band boundary is NOT neutralised (strict less-than gate).

    abs(pct_change_20d) == momentum_neutral_band_pct is outside the dead zone —
    the gate is ``abs(pct20) < band``, so equality passes through to sign logic.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.02, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.02),
    )
    # abs(0.02) < 0.02 is False → sign logic fires → bullish
    assert v.lean == "bullish", (
        f"Boundary value 0.02 should be directional (strict less-than), got {v.lean!r}"
    )


def test_neutral_band_zero_band_preserves_existing_sign_behaviour():
    """With band=0.0 the gate never fires and behaviour is identical to the old sign logic.

    Regression guard: disabling the band (setting it to 0.0) must restore the
    original "any non-zero momentum is directional" semantics so that existing
    test values remain coherent.
    """
    bullish_v = derive_technical_verdict(
        _features(pct_change_20d=0.001, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.0),
    )
    bearish_v = derive_technical_verdict(
        _features(pct_change_20d=-0.001, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.0),
    )
    assert bullish_v.lean == "bullish"
    assert bearish_v.lean == "bearish"


def test_neutral_band_no_data_path_unaffected():
    """No-data fingerprint still fires regardless of the neutral band.

    When all three core indicators are absent/zero (the no-data condition),
    ``is_no_data=True`` must be returned — the band gate must not interfere.
    """
    feats = _features(rsi_14=0, atr_pct_14=0)
    feats.pop("pct_change_20d", None)   # absent key triggers the no-data branch

    v = derive_technical_verdict(feats, _h(momentum_neutral_band_pct=0.02))

    assert v.is_no_data is True
    assert v.lean == "neutral"
    assert v.magnitude == 0.0
    assert v.confidence == 0.0


def test_neutral_band_exact_zero_pct20_unaffected():
    """Exact-zero pct_change_20d (e.g. flat 20d) still produces neutral lean.

    abs(0.0) < any positive band → gate fires → lean neutral.
    This test is mostly a documentation check — the existing behaviour is
    preserved, and the gate makes it explicit rather than relying on sign20==0.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=0.0, pct_change_5d=0.0),
        _h(momentum_neutral_band_pct=0.02),
    )
    assert v.lean == "neutral"


# ---------------------------------------------------------------------------
# RSI mean-reversion neutralisation (two-tier RSI rule)
# ---------------------------------------------------------------------------
#
# When a bearish 20d-trend call fires and RSI is below ``rsi_mean_reversion``
# (default 35), the lean is downgraded to neutral.  This reflects the
# empirical observation that moderately-oversold names tend to mean-revert
# at the 20-day horizon rather than continuing to fall.
#
# The existing RSI<rsi_oversold capitulation flip (→ bullish) still wins for
# genuinely capitulating names because rsi_oversold (25) < rsi_mean_reversion
# (35) — they compose in the right order.
# ---------------------------------------------------------------------------

def test_rsi_mean_reversion_bearish_rsi30_becomes_neutral():
    """Bearish 20d trend + RSI 30 (< 35 threshold) → downgraded to neutral.

    RSI 30 is in the moderate-oversold band — anti-predictive at 20d horizon.
    The lean is downgraded and the ``rsi_moderate_oversold`` factor is appended.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=-0.03, rsi_14=30.0),
        _h(rsi_mean_reversion=35.0),
    )
    assert v.lean == "neutral", (
        f"Expected neutral for bearish + RSI 30, got {v.lean!r}"
    )
    assert "rsi_moderate_oversold" in v.key_factors, (
        "Expected rsi_moderate_oversold factor when lean was neutralised"
    )


def test_rsi_mean_reversion_bearish_rsi45_stays_bearish():
    """Bearish 20d trend + RSI 45 (≥ 35 threshold) → stays bearish.

    RSI 45 is above the moderate-oversold threshold, so the rule does not
    fire and the plain bearish 20d-trend call is preserved.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=-0.03, rsi_14=45.0),
        _h(rsi_mean_reversion=35.0),
    )
    assert v.lean == "bearish", (
        f"Expected bearish for RSI 45 (above threshold), got {v.lean!r}"
    )
    assert "rsi_moderate_oversold" not in v.key_factors


def test_rsi_mean_reversion_capitulation_still_wins():
    """Bearish + RSI 20 + pct5 < 0 → bullish (capitulation flip still wins).

    RSI 20 is below both the mean-reversion threshold (35) and the oversold
    threshold (25).  The two-tier rule first neutralises the bearish call, then
    the capitulation branch (RSI < rsi_oversold AND pct5 < 0) re-promotes it
    to bullish — confirming the composition is correct.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=-0.04, rsi_14=20.0),
        _h(rsi_mean_reversion=35.0),
    )
    assert v.lean == "bullish", (
        f"Expected capitulation bullish flip for RSI 20 + pct5 < 0, got {v.lean!r}"
    )
    # Both factors should appear: the neutralisation and the oversold tag.
    assert "rsi_moderate_oversold" in v.key_factors
    assert "rsi_oversold" in v.key_factors


def test_rsi_mean_reversion_rsi20_pct5_nonnegative_stays_neutral():
    """Bearish + RSI 20 + pct5 ≥ 0 → neutral (no capitulation flip).

    RSI 20 triggers the mean-reversion neutralisation.  The capitulation branch
    requires ``pct5 < 0`` which is not satisfied here, so the lean stays at
    neutral after the neutralisation — it is not re-promoted to bullish.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=0.01, rsi_14=20.0),
        _h(rsi_mean_reversion=35.0),
    )
    assert v.lean == "neutral", (
        f"Expected neutral for RSI 20 + pct5 ≥ 0 (no capitulation), got {v.lean!r}"
    )
    assert "rsi_moderate_oversold" in v.key_factors


def test_rsi_mean_reversion_disabled_bearish_unaffected():
    """With rsi_mean_reversion=0.0, bearish calls are never neutralised by this rule.

    Setting the threshold to 0.0 disables the rule entirely: the condition
    ``rsi < 0.0`` cannot be true for any real RSI value (range 0–100), so
    bearish calls with moderate RSI propagate unchanged.
    """
    v = derive_technical_verdict(
        _features(pct_change_20d=-0.08, pct_change_5d=-0.03, rsi_14=30.0),
        _h(rsi_mean_reversion=0.0),
    )
    assert v.lean == "bearish", (
        f"Expected bearish when rule disabled (rsi_mean_reversion=0), got {v.lean!r}"
    )
    assert "rsi_moderate_oversold" not in v.key_factors
