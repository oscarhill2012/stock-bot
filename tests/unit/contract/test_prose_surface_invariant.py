"""Unit tests for the AnalystVerdict prose-surface invariant.

A non-no-data verdict must carry exactly one prose surface — rationale
(deterministic extractors) or report (LLM analysts) — never both, never
neither. ``is_no_data=True`` short-circuits the check.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver

_REPORT = AnalystReport(
    summary="Two drivers converging negative.",
    drivers=[
        ReportDriver(name="rsi", direction="bear", weight=0.5, body="rsi 78"),
        ReportDriver(name="trend", direction="bear", weight=0.5, body="20d -4%"),
    ],
)


def test_rationale_only_is_valid() -> None:
    """Deterministic verdict: rationale carries the one-liner, report is None."""
    v = AnalystVerdict(
        lean="bullish", magnitude=0.3, confidence=0.6,
        rationale="trend_up_20d, momentum_agree",
    )
    assert v.rationale == "trend_up_20d, momentum_agree"
    assert v.report is None


def test_report_only_is_valid() -> None:
    """LLM verdict: report carries the prose, rationale is the empty default."""
    v = AnalystVerdict(
        lean="bearish", magnitude=0.4, confidence=0.7,
        report=_REPORT,
    )
    assert v.rationale == ""
    assert v.report is _REPORT


def test_both_prose_surfaces_rejected() -> None:
    """A verdict carrying both rationale AND report is the old synthetic-prose
    bug; the new invariant rejects it loudly."""
    with pytest.raises(ValidationError, match="exactly one prose surface"):
        AnalystVerdict(
            lean="bullish",
            magnitude=0.3,
            confidence=0.6,
            rationale="trend_up_20d",
            report=_REPORT,
        )


def test_no_prose_surface_rejected_when_data_present() -> None:
    """Non-no-data verdict with neither rationale nor report → raises."""
    with pytest.raises(ValidationError, match="prose surface"):
        AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0)


def test_no_data_short_circuits_invariant() -> None:
    """is_no_data=True with rationale-only (the canonical no-data shape) is OK
    even though report is None."""
    v = AnalystVerdict(
        lean="neutral", magnitude=0.0, confidence=0.0,
        rationale="no price data",
        is_no_data=True,
    )
    assert v.is_no_data is True
    assert v.report is None
