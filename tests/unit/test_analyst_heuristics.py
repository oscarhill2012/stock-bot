"""Tier-1 unit tests for the analyst-heuristics loader.

Validates schema correctness, range enforcement, and that the loader is
cached (lru_cache) so changing the file after first load does not refresh
the in-process value.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.analysts.heuristics import (
    AnalystHeuristics,
    FundamentalVocabulary,
    NewsVocabulary,
    SmartMoneyHeuristics,
    SocialHeuristics,
    TechnicalHeuristics,
    load_heuristics,
)


def _valid_payload() -> dict:
    """Return a fully populated, valid heuristics payload."""
    return {
        "technical": {
            "trend_weight": 0.50,
            "anchor_52w_weight": 0.25,
            "rel_strength_weight": 0.25,
            "composite_neutral_band": 0.10,
            "horizon_days": 60,
            "vol_regime_window": 60,
            "vol_regime_extreme_z": 1.5,
            "vol_ratio_breakout": 1.3,
            "vol_ratio_dry_up": 0.7,
            "near_52w_extreme_pct": 5.0,
            "magnitude_cap": 1.0,
            "beta_confidence_damping_enabled": False,
        },
        "social": {
            "score_neutral_band": 0.05, "score_to_magnitude_scale": 2.0,
            "high_volume_mentions": 200, "high_volume_magnitude_boost": 0.15,
            "confidence_volume_floor": 30,
            "platform_disagreement_threshold": 0.3,
            "confidence_base": 0.4, "confidence_boost_step": 0.2,
            "confidence_penalty_step": 0.2, "magnitude_cap": 1.0,
        },
        "smart_money": {
            "multi_filer_min_count": 3, "high_activity_trade_count": 5,
            "lone_filer_confidence_floor": 0.1,
            "consensus_confidence_ceiling": 0.9, "magnitude_cap": 1.0,
        },
        "fundamental_vocabulary": {
            "guidance": ["raised", "maintained", "lowered", "none"],
            "tone": ["confident", "cautious", "defensive", "mixed"],
            "risks": ["regulatory", "litigation", "going_concern"],
            "insider_signals": ["cluster_buying", "cluster_selling", "mixed"],
        },
        "news_vocabulary": {
            "catalysts": ["earnings", "guidance", "none"],
            "novelty": ["high", "medium", "low"],
            "direction": ["positive", "negative", "mixed", "none"],
        },
        "golden_set": {"min_direction_agreement_pct": 70},
    }


def test_valid_payload_parses() -> None:
    """A complete, valid payload validates without error."""
    h = AnalystHeuristics.model_validate(_valid_payload())
    assert isinstance(h.technical, TechnicalHeuristics)
    assert isinstance(h.social, SocialHeuristics)
    assert isinstance(h.smart_money, SmartMoneyHeuristics)
    assert isinstance(h.fundamental_vocabulary, FundamentalVocabulary)
    assert isinstance(h.news_vocabulary, NewsVocabulary)


def test_trend_weight_out_of_range_rejected() -> None:
    """A composite trend weight above 1.0 must raise ValidationError."""
    payload = _valid_payload()
    payload["technical"]["trend_weight"] = 1.5
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_composite_weights_must_sum_to_one() -> None:
    """A mis-specified composite weight sum must raise ValidationError."""
    payload = _valid_payload()
    payload["technical"]["trend_weight"] = 0.60  # 0.60 + 0.25 + 0.25 = 1.10
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_confidence_base_out_of_range_rejected() -> None:
    """Confidence base outside [0, 1] must raise ValidationError."""
    payload = _valid_payload()
    payload["social"]["confidence_base"] = 1.5
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_missing_section_rejected() -> None:
    """Omitting a top-level section must raise ValidationError."""
    payload = _valid_payload()
    del payload["social"]
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_unknown_field_rejected() -> None:
    """Unknown keys must raise (extra='forbid')."""
    payload = _valid_payload()
    payload["technical"]["unknown_knob"] = 42
    with pytest.raises(ValidationError):
        AnalystHeuristics.model_validate(payload)


def test_load_heuristics_reads_config_file(tmp_path: Path, monkeypatch) -> None:
    """`load_heuristics()` reads the on-disk JSON and validates it."""
    cfg = tmp_path / "analyst_heuristics.json"
    cfg.write_text(json.dumps(_valid_payload()))
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(cfg))
    load_heuristics.cache_clear()
    h = load_heuristics()
    assert h.technical.horizon_days == 60


def test_load_heuristics_is_cached(tmp_path: Path, monkeypatch) -> None:
    """A second call without cache_clear() returns the same cached object.

    Verifies that mutating the file after first load does not refresh
    the in-process value — hot-reload is intentionally not supported.
    """
    cfg = tmp_path / "analyst_heuristics.json"
    cfg.write_text(json.dumps(_valid_payload()))
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(cfg))
    load_heuristics.cache_clear()

    first = load_heuristics()

    # Overwrite the file with a different value — the cache must ignore this.
    cfg.write_text(
        json.dumps({**_valid_payload(), "golden_set": {"min_direction_agreement_pct": 99}})
    )

    second = load_heuristics()
    assert first is second


def test_load_heuristics_missing_file_raises(tmp_path: Path, monkeypatch) -> None:
    """`load_heuristics()` raises FileNotFoundError when the path does not exist."""
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(tmp_path / "nonexistent.json"))
    load_heuristics.cache_clear()
    with pytest.raises(FileNotFoundError):
        load_heuristics()


def test_technical_heuristics_composite_surface():
    """The technical config exposes composite-weight knobs and drops reversal ones."""
    h = load_heuristics().technical

    # Composite vote weights — sum to 1.0.
    assert 0.0 <= h.trend_weight <= 1.0
    assert 0.0 <= h.anchor_52w_weight <= 1.0
    assert 0.0 <= h.rel_strength_weight <= 1.0
    assert abs((h.trend_weight + h.anchor_52w_weight + h.rel_strength_weight) - 1.0) < 1e-9

    assert 0.0 <= h.composite_neutral_band <= 1.0
    assert 1 <= h.horizon_days <= 252

    # Retained context knobs.
    assert h.near_52w_extreme_pct > 0.0
    assert 2 <= h.vol_regime_window <= 252
    assert h.vol_regime_extreme_z > 0.0
    assert 0.0 < h.magnitude_cap <= 1.0

    # Retired reversal knobs are gone (extra="forbid" rejects them in JSON too).
    for dead in (
        "reversal_neutral_band_pct", "reversal_magnitude_scale",
        "reversal_confidence_base", "reversal_horizon_days",
    ):
        assert not hasattr(h, dead), f"retired field still present: {dead}"
