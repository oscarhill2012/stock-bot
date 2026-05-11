"""AnalystVerdict + AnalystEvidence schema tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystVerdict


def _verdict(**overrides) -> AnalystVerdict:
    base = dict(
        lean="bullish",
        magnitude=0.5,
        confidence=0.7,
        rationale="RSI cooled + uptrend intact",
        key_factors=["rsi_14: 42"],
        is_no_data=False,
    )
    base.update(overrides)
    return AnalystVerdict(**base)


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=UTC)


def test_verdict_valid():
    v = _verdict()
    assert v.lean == "bullish"
    assert v.magnitude == 0.5
    assert v.confidence == 0.7
    assert v.key_factors == ["rsi_14: 42"]
    assert v.is_no_data is False


def test_verdict_neutral_no_data_flag():
    v = _verdict(lean="neutral", magnitude=0.0, confidence=0.0,
                 rationale="no filings", key_factors=[], is_no_data=True)
    assert v.is_no_data is True


def test_verdict_key_factors_default_empty():
    v = AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x")
    assert v.key_factors == []


def test_verdict_rejects_bad_lean():
    with pytest.raises(ValidationError):
        _verdict(lean="up")


def test_verdict_rejects_confidence_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(confidence=1.5)


def test_verdict_rejects_magnitude_out_of_range():
    with pytest.raises(ValidationError):
        _verdict(magnitude=1.5)


def test_verdict_rejects_rationale_over_160_chars():
    with pytest.raises(ValidationError):
        _verdict(rationale="x" * 161)


def test_evidence_valid():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="2026-05-08T14:00:00Z",
        recorded_at=_now(),
        features={"rsi_14": 42.3, "pct_change_5d": -0.018},
        feature_warnings=[],
        verdict=_verdict(lean="bearish", magnitude=0.4, confidence=0.6, rationale="weakening"),
    )
    assert e.ticker == "AAPL"
    assert e.analyst == "technical"
    assert e.tick_id == "2026-05-08T14:00:00Z"
    assert e.features["rsi_14"] == 42.3
    assert e.feature_warnings == []


def test_evidence_feature_warnings_default_empty():
    e = AnalystEvidence(
        ticker="AAPL",
        analyst="technical",
        tick_id="t",
        recorded_at=_now(),
        features={},
        verdict=_verdict(),
    )
    assert e.feature_warnings == []


def test_evidence_rejects_unknown_analyst():
    with pytest.raises(ValidationError):
        AnalystEvidence(
            ticker="AAPL",
            analyst="macro",
            tick_id="t",
            recorded_at=_now(),
            features={},
            verdict=_verdict(lean="neutral", magnitude=0.0, confidence=0.0, rationale="x"),
        )


def test_evidence_round_trip():
    original = AnalystEvidence(
        ticker="MSFT",
        analyst="fundamental",
        tick_id="2026-05-08T15:00:00Z",
        recorded_at=_now(),
        features={"pe_trailing": 32.5, "fcf_yield_pct": 2.4},
        feature_warnings=["pe_forward unavailable"],
        verdict=_verdict(lean="neutral", magnitude=0.1, confidence=0.4,
                         rationale="balanced", key_factors=["pe_trailing: 32.5"]),
    )
    dumped = original.model_dump(mode="json")
    rebuilt = AnalystEvidence.model_validate(dumped)
    assert rebuilt == original
