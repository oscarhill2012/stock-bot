"""D1.1 — schema-level enforcement that ``report`` accompanies non-no-data verdicts.

The News and Fundamental analyst LLMs were silently emitting
``report: null`` on a non-trivial fraction of ``is_no_data=False`` verdicts
in the baseline-2025-09 run (30.7 % and 3.6 % respectively).  The schema
previously declared ``report: AnalystReport | None = None``, which made
``report=None`` *valid*; the prompt instructed the LLM otherwise but the
schema did not enforce.

This module covers the new ``model_validator`` that rejects the
``is_no_data=False, report=None`` combination at the contract boundary.
``llm_retry`` already classifies ``pydantic.ValidationError`` as retryable,
so an offending LLM response triggers ADK's existing retry path
automatically.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver


def _valid_report() -> AnalystReport:
    """Build a minimal valid ``AnalystReport`` for round-trip tests."""

    return AnalystReport(
        summary="Test summary — exercises the report-required validator.",
        drivers=[
            ReportDriver(name="driver-one", direction="bull", weight=0.6, body="Body one."),
            ReportDriver(name="driver-two", direction="bear", weight=0.4, body="Body two."),
        ],
    )


def test_report_required_when_data_present_raises() -> None:
    """``is_no_data=False`` with ``report=None`` must fail schema validation."""

    with pytest.raises(ValidationError) as excinfo:
        AnalystVerdict.model_validate(
            {
                "lean":        "bullish",
                "magnitude":   0.5,
                "confidence":  0.6,
                "rationale":   "x",
                "key_factors": [],
                "is_no_data":  False,
                "report":      None,
            }
        )

    assert "report is required" in str(excinfo.value)


def test_report_required_when_no_data_allows_none() -> None:
    """``is_no_data=True`` with ``report=None`` is the genuine no-data case."""

    v = AnalystVerdict.model_validate(
        {
            "lean":        "neutral",
            "magnitude":   0.0,
            "confidence":  0.0,
            "rationale":   "no data",
            "key_factors": [],
            "is_no_data":  True,
            "report":      None,
        }
    )
    assert v.report is None
    assert v.is_no_data is True


def test_valid_verdict_with_report_round_trips() -> None:
    """A populated report round-trips through ``model_validate`` unchanged."""

    payload = {
        "lean":        "bullish",
        "magnitude":   0.5,
        "confidence":  0.6,
        "rationale":   "Positive guidance signal.",
        "key_factors": [],
        "is_no_data":  False,
        "report":      _valid_report().model_dump(),
    }
    v = AnalystVerdict.model_validate(payload)
    assert v.report is not None
    assert v.report.summary.startswith("Test summary")
