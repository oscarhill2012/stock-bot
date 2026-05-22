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
    """An LLM-shaped dict with closed-vocab tags parses cleanly to AnalystVerdict.

    The ``report`` block is now required whenever ``is_no_data=False`` (D1.1),
    so the canned dict must include it to represent a well-formed LLM output.
    """
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
        "report": {
            "summary": "Raised guidance and CEO/CFO cluster buying together signal a bullish outlook.",
            "drivers": [
                {"name": "guidance:raised",          "direction": "bull", "weight": 0.5, "body": "Management raised forward guidance, signalling internal confidence."},
                {"name": "insider:cluster_buying",   "direction": "bull", "weight": 0.5, "body": "CEO and CFO bought shares in the same window — cluster signal."},
            ],
        },
    }
    verdict = AnalystVerdict.model_validate(raw)
    assert verdict.lean == "bullish"
    assert verdict.magnitude == pytest.approx(0.6)
    assert verdict.confidence == pytest.approx(0.7)
    assert "guidance:raised" in verdict.key_factors


def test_canned_bearish_verdict_with_insider_tags_validates() -> None:
    """A bearish verdict with multiple insider + risk tags validates correctly.

    The ``report`` block is now required whenever ``is_no_data=False`` (D1.1).
    """
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
        "report": {
            "summary": "Lowered guidance, defensive tone, and insider sales collectively point bearish.",
            "drivers": [
                {"name": "guidance:lowered",                   "direction": "bear", "weight": 0.4, "body": "Management cut forward guidance, citing macro headwinds."},
                {"name": "insider:discretionary_sale_dominant","direction": "bear", "weight": 0.6, "body": "Discretionary insider sales dominate; no compensating buys observed."},
            ],
        },
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
    """A rationale exceeding the *schema* cap is rejected.

    The schema cap is the prompt-facing cap from
    ``config/analysts.json::output_caps.verdict_rationale_max_chars``
    plus ``slack_percent`` headroom — see the "two-tier convention" note
    in ``src/config/strategist.py``.  Reads the live schema cap from
    config so retuning either the prompt cap or the slack does not
    silently invalidate this regression.
    """
    from config.analysts import get_analysts_config

    cfg        = get_analysts_config()
    schema_cap = cfg.schema_cap(cfg.output_caps.verdict_rationale_max_chars)
    long_rationale = "x" * (schema_cap + 1)  # one char over the *schema* (slack-applied) cap

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
