"""build_ticker_evidence aggregator tests — Tier 1, no LLM."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest

from contract.digest import DEFAULT_ANALYST_WEIGHTS, build_ticker_evidence
from contract.evidence import AnalystEvidence, AnalystVerdict


def _now():
    return datetime(2026, 5, 8, 14, 0, tzinfo=UTC)


def _ev(
    analyst: str, lean: str, conf: float, ticker: str = "AAPL", magnitude: float | None = None
) -> AnalystEvidence:
    """Build a deterministic-style AnalystEvidence for aggregation tests.

    Digest tests exercise aggregation maths, not prose content.  All verdicts
    are built as deterministic-extractor style: rationale carries the one-liner
    and ``report`` is ``None``.  This satisfies the exactly-one-prose-surface
    invariant without introducing LLM-report scaffolding.

    Parameters
    ----------
    analyst:
        Analyst key (e.g. ``"technical"``).
    lean:
        Direction string — ``"bullish"``, ``"bearish"``, or ``"neutral"``.
    conf:
        Confidence value in ``[0, 1]``.  Also used as magnitude unless
        ``magnitude`` is supplied explicitly.
    ticker:
        Stock ticker symbol (default ``"AAPL"``).
    magnitude:
        Override for magnitude when it should differ from confidence.

    Returns
    -------
    AnalystEvidence
        Fully-formed evidence object with a rationale-only verdict.
    """
    return AnalystEvidence(
        ticker=ticker,
        analyst=analyst,
        tick_id="t",
        recorded_at=_now(),
        features={},
        verdict=AnalystVerdict(
            lean=lean,
            magnitude=conf if magnitude is None else magnitude,
            confidence=conf,
            rationale="digest-test deterministic verdict",
            key_factors=[],
            is_no_data=False,
            report=None,
        ),
    )


# ── Lean sign + dead zone ─────────────────────────────────────────────────────


def test_all_bullish_high_confidence_aggregates_bullish():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.8),
        "fundamental": _ev("fundamental", "bullish", 0.7),
        "news": _ev("news", "bullish", 0.6),
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
        "fundamental": _ev("fundamental", "bearish", 0.1),
        "news": _ev("news", "bearish", 0.1),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


def test_one_strong_bullish_beats_two_weak_neutrals_outside_dead_zone():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.95),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "news": _ev("news", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "bullish"


def test_dead_zone_collapses_marginally_positive_to_neutral():
    # With 3 analysts and denominator = 3.0, a confidence of 0.4 yields
    # magnitude = 0.4 / 3 ≈ 0.133, which is below DIRECTION_DEAD_ZONE (0.15).
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.4),
        "fundamental": _ev("fundamental", "neutral", 0.0),
        "news": _ev("news", "neutral", 0.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.lean == "neutral"


# ── Aggregate confidence (mean of contributing analysts) ──────────────────────


def test_aggregate_confidence_is_mean_of_contributing_analysts():
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.4),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "news": _ev("news", "bullish", 0.8),
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
        "news": _ev("news", "bearish", 0.6),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert "2" in te.aggregate.summary
    assert "bullish" in te.aggregate.summary.lower()


# ── Disagreement (lives on aggregate) ─────────────────────────────────────────


def test_unanimous_agreement_disagreement_zero():
    per_analyst = {a: _ev(a, "bullish", 0.7) for a in DEFAULT_ANALYST_WEIGHTS}
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.aggregate.disagreement < 0.01


def test_max_split_disagreement_high():
    # One bullish, two bearish — distinct signed confidences → non-trivial variance.
    per_analyst = {
        "technical": _ev("technical", "bullish", 1.0),
        "fundamental": _ev("fundamental", "bearish", 1.0),
        "news": _ev("news", "bearish", 1.0),
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

    # The two absent analysts (fundamental, news) should be neutral-filled.
    for missing in ("fundamental", "news"):
        assert te.per_analyst[missing].verdict.lean == "neutral"
        assert te.per_analyst[missing].verdict.magnitude == 0.0
        assert te.per_analyst[missing].verdict.confidence == 0.0
        assert te.per_analyst[missing].verdict.is_no_data is True


def test_no_data_flag_treated_as_neutral_in_aggregate():
    """An analyst evidence entry marked is_no_data must contribute 0.0 to the
    weighted sum, keeping the aggregate driven by the three contributing analysts.
    """
    per_analyst = {
        "technical": _ev("technical", "bullish", 0.6),
        "fundamental": _ev("fundamental", "bullish", 0.6),
        "news": AnalystEvidence(
            ticker="AAPL",
            analyst="news",
            tick_id="t",
            recorded_at=_now(),
            features={"is_no_data": 1.0},
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

    # news is_no_data → contributes 0.0; magnitude = (0.6 + 0.6) / 3 = 0.4.
    assert te.aggregate.magnitude == pytest.approx(0.4, rel=0.01)


# ── weights snapshotting (top-level on TickerEvidence) ────────────────────────


def test_weights_snapshotted_at_top_level():
    per_analyst = {a: _ev(a, "bullish", 0.5) for a in DEFAULT_ANALYST_WEIGHTS}
    custom = {"technical": 2.0, "fundamental": 1.0, "news": 0.5}
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
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)
    assert te.per_analyst["technical"].verdict.magnitude == pytest.approx(0.9)


# ── A-050: missing analyst slot must warn loudly ──────────────────────────────


def test_fill_missing_emits_structured_warning(caplog):
    """A missing analyst slot must be logged loudly AND synthesised with is_no_data=True (A-050).

    Two assertions verify the loud-failure policy:
    (a) A structured WARNING is emitted, naming the missing slot in the log message.
    (b) The synthesised AnalystEvidence's verdict has ``is_no_data=True``, confirming
        the synthesised neutral placeholder is clearly marked as a no-data record.

    Note: A-053 Branch B removed the machine-readable marker field.  The A-050
    missing-slot signal is now carried solely by the logger.warning call and
    the ``is_no_data=True`` flag.
    """
    # Only supply technical — fundamental and news are absent and should each
    # trigger a WARNING + is_no_data=True on the synthesised placeholder.
    per_analyst = {"technical": _ev("technical", "bullish", 0.8)}

    with caplog.at_level("WARNING"):
        te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)

    # (a) A structured WARNING must fire for EACH missing slot.  Both
    # "fundamental" and "news" are absent from the fixture, so we expect two
    # separate WARNING records — one per slot.  This confirms the operator log
    # fires for every pipeline gap, not just the first one encountered.
    warning_messages = [
        rec.getMessage()
        for rec in caplog.records
        if rec.levelname == "WARNING" and "missing_analyst_slot" in rec.getMessage()
    ]
    assert any("fundamental" in msg for msg in warning_messages), (
        "expected a structured WARNING naming the missing 'fundamental' slot"
    )
    assert any("news" in msg for msg in warning_messages), (
        "expected a structured WARNING naming the missing 'news' slot"
    )

    # (b) Both synthesised entries must be marked is_no_data=True — the sole
    # surviving machine-readable signal after A-053 Branch B removed the
    # feature_warnings marker.
    assert te.per_analyst["fundamental"].verdict.is_no_data is True, (
        "synthesised 'fundamental' missing-slot entry must have is_no_data=True"
    )
    assert te.per_analyst["news"].verdict.is_no_data is True, (
        "synthesised 'news' missing-slot entry must have is_no_data=True"
    )


# ── Dilution-fix regression: 3 unanimous analysts must reach magnitude 1.0 ────


def test_three_unanimous_bullish_analysts_magnitude_is_one():
    """Regression guard for the phantom-slot dilution bug.

    Before the fix, DEFAULT_ANALYST_WEIGHTS had 5 entries (technical,
    fundamental, news, social, smart_money) but only 3 could ever contribute.
    The denominator was always 5.0 so three unanimous bullish analysts at
    confidence 1.0 produced magnitude 3/5 = 0.6 instead of 1.0.

    With the phantom entries removed the denominator matches the contributor
    count and magnitude reaches 1.0.
    """
    per_analyst = {
        "technical":   _ev("technical",   "bullish", 1.0),
        "fundamental": _ev("fundamental", "bullish", 1.0),
        "news":        _ev("news",        "bullish", 1.0),
    }
    te = build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)

    assert te.aggregate.lean == "bullish"
    assert te.aggregate.magnitude == pytest.approx(1.0, abs=1e-9), (
        "three unanimous bullish analysts at confidence 1.0 must produce magnitude "
        "1.0 — if this is 0.6 then phantom slots are back in DEFAULT_ANALYST_WEIGHTS"
    )


# ── No spurious missing-slot warnings on a normal tick ───────────────────────


def test_normal_tick_no_missing_slot_warning(caplog):
    """A tick supplying all three wired analysts must produce ZERO
    missing_analyst_slot WARNINGs (A-050 fires only on genuine pipeline gaps).

    Also asserts that DEFAULT_ANALYST_WEIGHTS contains exactly the three
    expected keys — a canary that breaks loudly if a phantom slot re-enters
    the expected-set without the corresponding pipeline wiring.
    """
    assert set(DEFAULT_ANALYST_WEIGHTS.keys()) == {"technical", "fundamental", "news"}, (
        "DEFAULT_ANALYST_WEIGHTS must contain exactly the three wired+consumed "
        "analysts; adding a phantom entry without pipeline wiring causes both "
        "false-positive warnings and aggregate-magnitude dilution"
    )

    per_analyst = {
        "technical":   _ev("technical",   "bullish", 0.7),
        "fundamental": _ev("fundamental", "bullish", 0.7),
        "news":        _ev("news",        "bullish", 0.7),
    }

    with caplog.at_level(logging.WARNING, logger="contract.digest"):
        build_ticker_evidence(per_analyst, "AAPL", "t", _now(), DEFAULT_ANALYST_WEIGHTS)

    spurious = [
        rec for rec in caplog.records
        if rec.levelname == "WARNING" and "missing_analyst_slot" in rec.getMessage()
    ]
    assert spurious == [], (
        f"unexpected missing_analyst_slot warnings on a complete tick: {spurious}"
    )


# ── Ticker / tick_id / recorded_at carry-through ──────────────────────────────


def test_metadata_propagated():
    per_analyst = {a: _ev(a, "neutral", 0.0, ticker="MSFT") for a in DEFAULT_ANALYST_WEIGHTS}
    when = datetime(2026, 5, 8, 16, 30, tzinfo=UTC)
    te = build_ticker_evidence(per_analyst, "MSFT", "tick_42", when, DEFAULT_ANALYST_WEIGHTS)
    assert te.ticker == "MSFT"
    assert te.tick_id == "tick_42"
    assert te.recorded_at == when
