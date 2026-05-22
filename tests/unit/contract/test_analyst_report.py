"""Unit tests for the AnalystReport / ReportDriver schemas."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystReport, AnalystVerdict, ReportDriver


def _driver(
    name: str = "EU App Store ruling",
    direction: str = "bear",
    weight: float = 0.5,
) -> ReportDriver:
    """Construct a valid ReportDriver with sensible defaults.

    Parameters
    ----------
    name:
        Short label for the driver (4-6 words).
    direction:
        Directional signal — one of "bull", "bear", or "neutral".
    weight:
        Relative importance in [0, 1].

    Returns
    -------
    ReportDriver
        A validated driver instance.
    """
    return ReportDriver(name=name, direction=direction, weight=weight, body="x" * 50)


def test_report_round_trips() -> None:
    """Two drivers, summary populated -> survives model_dump round-trip."""
    rpt = AnalystReport(
        summary="Two converging negatives this tick.",
        drivers=[_driver(), _driver(name="Gemini push", weight=0.3)],
    )
    restored = AnalystReport.model_validate(rpt.model_dump())
    assert restored == rpt


def test_report_rejects_empty_drivers() -> None:
    """An LLM emitting zero drivers fails — the prompt mandates 2-4."""
    with pytest.raises(ValidationError):
        AnalystReport(summary="x", drivers=[])


def test_report_rejects_single_driver() -> None:
    """A single driver fails — the prompt mandates 2-4 entries."""
    with pytest.raises(ValidationError):
        AnalystReport(summary="x", drivers=[_driver()])


def test_report_rejects_too_many_drivers() -> None:
    """More than 4 drivers is dilution — reject."""
    drivers = [_driver(name=f"D{i}") for i in range(5)]
    with pytest.raises(ValidationError):
        AnalystReport(summary="x", drivers=drivers)


def test_driver_weight_outside_unit_range_rejected() -> None:
    """A driver weight must lie in [0, 1]."""
    with pytest.raises(ValidationError):
        ReportDriver(name="x", direction="bull", weight=1.5, body="y")


def test_driver_direction_closed_vocabulary() -> None:
    """Direction is restricted to bull/bear/neutral."""
    with pytest.raises(ValidationError):
        ReportDriver(name="x", direction="sideways", weight=0.5, body="y")  # type: ignore[arg-type]


def test_verdict_report_field_defaults_to_none() -> None:
    """No-data verdicts (is_no_data=True) carry no report block — that is the
    correct path for deterministic analysts and genuine empty-data cases."""
    v = AnalystVerdict(
        lean="neutral",
        magnitude=0.0,
        confidence=0.0,
        rationale="x",
        key_factors=[],
        is_no_data=True,    # required by D1.1 validator when report=None
    )
    assert v.report is None
