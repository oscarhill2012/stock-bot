"""Schema-validation tests for canned Fundamental LLM output shapes.

These tests verify that the ``AnalystVerdict`` Pydantic schema correctly
accepts well-formed LLM outputs and rejects malformed ones.  No LLM is
called — the canned dicts represent the shapes a real Fundamental LLM would
emit after following the closed-vocab prompt.

Tag-vocabulary adherence (e.g. ``guidance:raised`` must be a known value) is
a runtime check performed by the surface-trace harness, not a schema check.
The schema tests here confirm structural validity only.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystVerdict


def test_canned_good_verdict_validates() -> None:
    """An LLM-shaped dict with closed-vocab tags parses cleanly to AnalystVerdict."""
    raw = {
        "lean": "bullish",
        "magnitude": 0.6,
        "confidence": 0.7,
        "rationale": "raised guidance + cluster_buying by CEO and CFO",
        "key_factors": [
            "guidance:raised",
            "tone:confident",
            "insider:cluster_buying",
        ],
        "is_no_data": False,
    }
    verdict = AnalystVerdict.model_validate(raw)
    assert verdict.lean == "bullish"
    assert verdict.magnitude == pytest.approx(0.6)
    assert verdict.confidence == pytest.approx(0.7)
    assert "guidance:raised" in verdict.key_factors


def test_canned_bearish_verdict_with_insider_tags_validates() -> None:
    """A bearish verdict with multiple insider + risk tags validates correctly."""
    raw = {
        "lean": "bearish",
        "magnitude": 0.75,
        "confidence": 0.65,
        "rationale": "discretionary_sale_dominant + lowered guidance + going_concern",
        "key_factors": [
            "guidance:lowered",
            "tone:defensive",
            "insider:discretionary_sale_dominant",
            "going_concern:true",
            "risk:debt_refinance",
        ],
        "is_no_data": False,
    }
    verdict = AnalystVerdict.model_validate(raw)
    assert verdict.lean == "bearish"
    assert len(verdict.key_factors) == 5


def test_canned_no_data_verdict_validates() -> None:
    """A no-data sentinel verdict (all zeroes) validates."""
    raw = {
        "lean": "neutral",
        "magnitude": 0.0,
        "confidence": 0.0,
        "rationale": "no filings and no insider activity",
        "key_factors": [],
        "is_no_data": True,
    }
    verdict = AnalystVerdict.model_validate(raw)
    assert verdict.is_no_data is True
    assert verdict.magnitude == pytest.approx(0.0)


def test_canned_bad_verdict_with_out_of_range_magnitude_rejected() -> None:
    """magnitude > 1.0 is rejected by the schema's Field(le=1.0) constraint."""
    raw = {
        "lean": "bullish",
        "magnitude": 1.5,
        "confidence": 0.7,
        "rationale": "x",
        "key_factors": [],
        "is_no_data": False,
    }
    with pytest.raises(ValidationError):
        AnalystVerdict.model_validate(raw)


def test_canned_bad_verdict_invalid_lean_rejected() -> None:
    """An invalid ``lean`` value (not bullish/bearish/neutral) is rejected."""
    raw = {
        "lean": "very_bullish",
        "magnitude": 0.5,
        "confidence": 0.5,
        "rationale": "x",
        "key_factors": [],
        "is_no_data": False,
    }
    with pytest.raises(ValidationError):
        AnalystVerdict.model_validate(raw)


def test_canned_bad_verdict_rationale_too_long_rejected() -> None:
    """A rationale exceeding 160 characters is rejected by the schema."""
    long_rationale = "x" * 161
    raw = {
        "lean": "neutral",
        "magnitude": 0.3,
        "confidence": 0.3,
        "rationale": long_rationale,
        "key_factors": [],
        "is_no_data": False,
    }
    with pytest.raises(ValidationError):
        AnalystVerdict.model_validate(raw)


def test_canned_bad_verdict_too_many_key_factors_rejected() -> None:
    """More than 8 key_factors entries are rejected by the schema."""
    raw = {
        "lean": "bullish",
        "magnitude": 0.5,
        "confidence": 0.5,
        "rationale": "too many factors",
        "key_factors": [f"factor:{i}" for i in range(9)],
        "is_no_data": False,
    }
    with pytest.raises(ValidationError):
        AnalystVerdict.model_validate(raw)
