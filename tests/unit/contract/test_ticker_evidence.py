"""TickerEvidence + AggregateVerdict tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from contract.evidence import AnalystEvidence, AnalystReport, AnalystVerdict, ReportDriver
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _now() -> datetime:
    return datetime(2026, 5, 8, 14, 0, tzinfo=UTC)


# Stub report satisfying the D1.1 validator for is_no_data=False verdicts.
_STUB_REPORT = AnalystReport(
    summary="Stub report for ticker-evidence schema tests.",
    drivers=[
        ReportDriver(name="driver-a", direction="bull", weight=0.6, body="Stub body A."),
        ReportDriver(name="driver-b", direction="bear", weight=0.4, body="Stub body B."),
    ],
)


def _ev(analyst: str, lean: str, magnitude: float, confidence: float) -> AnalystEvidence:
    return AnalystEvidence(
        ticker="AAPL",
        analyst=analyst,
        tick_id="tick_X",
        recorded_at=_now(),
        features={},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean, magnitude=magnitude, confidence=confidence,
            rationale="x", key_factors=[], is_no_data=False,
            report=_STUB_REPORT,
        ),
    )


def _agg(**overrides) -> AggregateVerdict:
    base = dict(lean="bullish", magnitude=0.42, confidence=0.6,
                disagreement=0.1, summary="3/4 bullish, 1 neutral")
    base.update(overrides)
    return AggregateVerdict(**base)


def test_aggregate_valid():
    a = _agg()
    assert a.lean == "bullish"
    assert a.magnitude == 0.42
    assert a.confidence == 0.6
    assert a.disagreement == 0.1
    assert a.summary.startswith("3/4")


def test_aggregate_rejects_bad_magnitude():
    with pytest.raises(ValidationError):
        _agg(magnitude=1.5)


def test_aggregate_rejects_bad_disagreement():
    with pytest.raises(ValidationError):
        _agg(disagreement=1.5)


def test_aggregate_rejects_bad_confidence():
    with pytest.raises(ValidationError):
        _agg(confidence=-0.1)


def test_aggregate_summary_default_empty():
    a = AggregateVerdict(lean="neutral", magnitude=0.0, confidence=0.0, disagreement=0.0)
    assert a.summary == ""


def test_ticker_evidence_valid():
    te = TickerEvidence(
        ticker="AAPL",
        tick_id="tick_X",
        recorded_at=_now(),
        per_analyst={
            "technical": _ev("technical", "bullish", 0.6, 0.6),
            "fundamental": _ev("fundamental", "bearish", 0.4, 0.4),
        },
        aggregate=_agg(lean="neutral", magnitude=0.1, confidence=0.5, disagreement=0.55,
                       summary="split"),
        weights={"technical": 1.0, "fundamental": 1.0},
    )
    assert te.ticker == "AAPL"
    assert "technical" in te.per_analyst
    assert te.weights["technical"] == 1.0


def test_ticker_evidence_round_trip():
    original = TickerEvidence(
        ticker="MSFT",
        tick_id="tick_Y",
        recorded_at=datetime(2026, 5, 8, 15, 0, tzinfo=UTC),
        per_analyst={"technical": _ev("technical", "bullish", 0.5, 0.5)},
        aggregate=_agg(lean="bullish", magnitude=0.5, confidence=0.5, disagreement=0.0,
                       summary="1 bullish"),
        weights={"technical": 1.0},
    )
    dumped = original.model_dump(mode="json")
    rebuilt = TickerEvidence.model_validate(dumped)
    assert rebuilt == original
