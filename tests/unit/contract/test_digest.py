"""build_ticker_evidence aggregator tests — Tier 1, no LLM."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contract.digest import build_ticker_evidence
from contract.digest_defaults import DEFAULT_ANALYST_WEIGHTS
from contract.evidence import AnalystEvidence, AnalystVerdict


def _now():
    return datetime(2026, 5, 8, 14, 0, tzinfo=UTC)


def _ev(
    analyst: str, lean: str, conf: float, ticker: str = "AAPL", magnitude: float | None = None
) -> AnalystEvidence:
    """Build an AnalystEvidence. By default magnitude == confidence (the LLM is
    instructed to keep them aligned unless it has a reason not to). Tests that
    care about the magnitude/confidence split pass `magnitude=` explicitly."""
    return AnalystEvidence(
        ticker=ticker,
        analyst=analyst,
        tick_id="t",
        recorded_at=_now(),
        features={},
        feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean,
            magnitude=conf if magnitude is None else magnitude,
            confidence=conf,
            rationale="x",
            key_factors=[],
            is_no_data=False,
        ),
    )


# ── Lean sign + dead zone ─────────────────────────────────────────────────────


def test_all_bullish_high_confidence_aggregates_bullish():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        "fundamental": _ev("fundamental", "bullish", 0.7),
        "news": _ev("news", "bullish", 0.6),
        "smart_money": _ev("smart_money", "bullish", 0.9),
    }
    te = build_ticker_evidence(
        per_analyst, ticker="AAPL", tick_id="t", recorded_at=_now(), weights=DEFAULT_ANALYST_WEIGHTS
    )
    assert te.aggregate.lean == "bullish"
    assert te.aggregate.magnitude > 0.5


def test_all_bearish_aggregates_bearish():
    per_analyst = {a: _ev(a, "bearish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bearish"


def test_split_low_confidence_falls_into_dead_zone_neutral():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.1),
        "fundamental": _ev("fundamental", "bullish", 0.1),
        "news": _ev("news", "bearish", 0.1),
        "smart_money": _ev("smart_money", "bearish", 0.1),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


def test_one_strong_bullish_beats_three_weak_neutrals_outside_dead_zone():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.95),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "news": _ev("news", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bullish"


def test_dead_zone_collapses_marginally_positive_to_neutral():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.5),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "news": _ev("news", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


# ── Aggregate confidence (mean of contributing analysts) ──────────────────────


def test_aggregate_confidence_is_mean_of_contributing_analysts():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.4),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "news": _ev("news", "bullish", 0.8),
        "smart_money": _ev("smart_money", "bullish", 0.6),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.confidence == pytest.approx(0.6, rel=0.01)


def test_aggregate_confidence_excludes_no_data_analysts():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.9),
        "fundamental": _ev("fundamental", "bullish", 0.9),
        "news": _ev("news", "bullish", 0.9),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.confidence == pytest.approx(0.9, rel=0.01)


# ── Aggregate summary (rendered string) ───────────────────────────────────────


def test_aggregate_summary_describes_lean_breakdown():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.6),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "news": _ev("news", "bullish", 0.6),
        "smart_money": _ev("smart_money", "bearish", 0.6),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert "3" in te.aggregate.summary
    assert "bullish" in te.aggregate.summary.lower()


# ── Disagreement (lives on aggregate) ─────────────────────────────────────────


def test_unanimous_agreement_disagreement_zero():
    per_analyst = {a: _ev(a, "bullish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.disagreement < 0.01


def test_max_split_disagreement_high():
    per_analyst = {
        "technical": _ev("technical", "bullish", 1.0),
        "fundamental": _ev("fundamental", "bullish", 1.0),
        "news": _ev("news", "bearish", 1.0),
        "smart_money": _ev("smart_money", "bearish", 1.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.disagreement > 0.5


# ── Missing analyst neutral-fill ──────────────────────────────────────────────


def test_missing_analysts_neutral_filled():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert set(te.per_analyst.keys()) == set(DEFAULT_ANALYST_WEIGHTS.keys())
    # Task 7 adds social; all four non-provided analysts should be neutral-filled.
    for missing in ("fundamental", "news", "social", "smart_money"):
        assert te.per_analyst[missing].verdict.lean == "neutral"
        assert te.per_analyst[missing].verdict.magnitude == 0.0
        assert te.per_analyst[missing].verdict.confidence == 0.0
        assert te.per_analyst[missing].verdict.is_no_data is True


def test_smart_money_no_data_flag_treated_as_neutral():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.6),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "news": _ev("news", "bullish", 0.6),
        "smart_money": AnalystEvidence(
            ticker="AAPL",
            analyst="smart_money",
            tick_id="t",
            recorded_at=_now(),
            features={"is_no_data": 1.0},
            feature_warnings=[],
            verdict=AnalystVerdict(
                lean="bullish",
                magnitude=0.9,
                confidence=0.9,
                rationale="x",
                key_factors=[],
                is_no_data=True,
            ),
        ),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bullish"
    # Task 7 adds social as a 5th analyst (is_no_data → 0.0 contribution).
    # Magnitude = (0.6 + 0.6 + 0.6) / 5 = 0.36.
    assert te.aggregate.magnitude == pytest.approx(0.36, rel=0.01)


# ── weights snapshotting (top-level on TickerEvidence) ────────────────────────


def test_weights_snapshotted_at_top_level():
    per_analyst = {a: _ev(a, "bullish", 0.5) for a in DEFAULT_ANALYST_WEIGHTS}
    custom = {"technical": 2.0, "fundamental": 1.0, "news": 0.5, "smart_money": 1.0}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), custom)
    assert te.weights == custom


# ── Per-analyst magnitude flows through unchanged ─────────────────────────────


def test_per_analyst_magnitude_preserved_in_dump():
    """Per-analyst `magnitude` must survive aggregation untouched — it's the
    substrate the future per-evidence-key weighting (B5) will learn against."""
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.7, magnitude=0.9),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "news": _ev("news", "neutral", 0.0),
        "smart_money": _ev("smart_money", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.per_analyst["technical"].verdict.magnitude == pytest.approx(0.9)


# ── Ticker / tick_id / recorded_at carry-through ──────────────────────────────


def test_metadata_propagated():
    per_analyst = {a: _ev(a, "neutral", 0.0, ticker="MSFT") for a in DEFAULT_ANALYST_WEIGHTS}
    when = datetime(2026, 5, 8, 16, 30, tzinfo=UTC)
    te = build_ticker_evidence(per_analyst, "MSFT", "tick_42", when, DEFAULT_ANALYST_WEIGHTS)
    assert te.ticker == "MSFT"
    assert te.tick_id == "tick_42"
    assert te.recorded_at == when
