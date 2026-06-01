"""Prose-surface invariant enforcement on AnalystVerdict.

The ``_prose_surface_required_when_data_present`` model_validator (Task 1)
enforces the exactly-one-prose-surface rule:

- Deterministic extractors: ``rationale`` non-empty, ``report=None``.
- LLM analysts: ``report`` present, ``rationale=""``.
- Carrying both is the old synthetic-prose pathology — rejected loudly.
- Carrying neither (non-no-data) is an incomplete verdict — rejected loudly.
- ``is_no_data=True`` short-circuits the check entirely.

This module pins the expected behaviour at the contract boundary.
``llm_retry`` already classifies ``pydantic.ValidationError`` as retryable,
so invalid LLM responses trigger ADK's existing retry path automatically.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver


def _valid_report() -> AnalystReport:
    """Build a minimal valid ``AnalystReport`` for round-trip tests."""

    return AnalystReport(
        summary="Test summary — exercises the prose-surface validator.",
        drivers=[
            ReportDriver(name="driver-one", direction="bull", weight=0.6, body="Body one."),
            ReportDriver(name="driver-two", direction="bear", weight=0.4, body="Body two."),
        ],
    )


def test_prose_surface_exactly_one_enforced() -> None:
    """The exactly-one-prose-surface invariant is enforced for non-no-data verdicts.

    Three cases are tested:

    1. Rationale-only (``report=None``, ``rationale`` non-empty) → valid.
       This is the deterministic-extractor shape.
    2. Both surfaces present → raises ``ValidationError`` matching
       "exactly one prose surface".
    3. Neither surface present → raises ``ValidationError`` matching
       "prose surface".
    """

    # ── Case 1: rationale-only is valid (deterministic-extractor shape) ──────
    v = AnalystVerdict.model_validate(
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
    assert v.is_no_data is False
    assert v.report is None, "deterministic verdict must not carry a report"
    assert v.rationale != "", "rationale carries the deterministic one-liner"

    # ── Case 2: both surfaces → rejected ─────────────────────────────────────
    with pytest.raises(ValidationError, match="exactly one prose surface"):
        AnalystVerdict.model_validate(
            {
                "lean":        "bullish",
                "magnitude":   0.5,
                "confidence":  0.6,
                "rationale":   "non-empty rationale alongside a report",
                "key_factors": [],
                "is_no_data":  False,
                "report":      _valid_report().model_dump(),
            }
        )

    # ── Case 3: neither surface → rejected ───────────────────────────────────
    with pytest.raises(ValidationError, match="prose surface"):
        AnalystVerdict.model_validate(
            {
                "lean":        "bullish",
                "magnitude":   0.5,
                "confidence":  0.6,
                "rationale":   "",
                "key_factors": [],
                "is_no_data":  False,
                "report":      None,
            }
        )


def test_no_data_allows_no_prose_surface() -> None:
    """``is_no_data=True`` with ``report=None`` is the genuine no-data case.

    The prose-surface check is short-circuited entirely; rationale may hold
    the no-data reason but is not required to be non-empty.
    """

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
    """An LLM-style verdict (report present, rationale blank) round-trips unchanged.

    The LLM analyst shape is ``report`` populated and ``rationale=""`` — only
    one prose surface.  The round-trip confirms the validator accepts it and
    the report content survives serialisation.
    """

    payload = {
        "lean":        "bullish",
        "magnitude":   0.5,
        "confidence":  0.6,
        "rationale":   "",
        "key_factors": [],
        "is_no_data":  False,
        "report":      _valid_report().model_dump(),
    }
    v = AnalystVerdict.model_validate(payload)
    assert v.report is not None
    assert v.report.summary.startswith("Test summary")
