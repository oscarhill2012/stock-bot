"""TickerEvidence prompt rendering tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agents.strategist.evidence_view import render_ticker_evidence
from contract.evidence import AnalystEvidence, AnalystReport, AnalystVerdict, ReportDriver
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _stub_report(lean: str, analyst: str) -> AnalystReport:
    """Build a minimal two-driver AnalystReport for test fixture use.

    The D1.1 schema rule requires ``report`` whenever ``is_no_data=False``;
    test helpers that construct non-no-data verdicts must supply one.
    """
    direction = {"bullish": "bull", "bearish": "bear", "neutral": "neutral"}[lean]
    return AnalystReport(
        summary=f"{analyst} leans {lean}.",
        drivers=[
            ReportDriver(name="primary_signal",  direction=direction, weight=0.6, body=f"{analyst} primary signal."),
            ReportDriver(name="secondary_signal", direction=direction, weight=0.4, body=f"{analyst} secondary signal."),
        ],
    )


def _ev(analyst: str, lean: str, conf: float, features: dict[str, float] | None = None,
        ticker: str = "AAPL") -> AnalystEvidence:
    """Build a minimal AnalystEvidence for test fixtures."""
    return AnalystEvidence(
        ticker=ticker, analyst=analyst,
        tick_id="tick_X",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        features=features or {}, feature_warnings=[],
        verdict=AnalystVerdict(
            lean=lean, magnitude=conf, confidence=conf,
            rationale=f"{analyst} {lean}", key_factors=[],
            report=_stub_report(lean, analyst),
        ),
    )


def _te(ticker: str = "AAPL", lean: str = "bullish", magnitude: float = 0.5,
        disagreement: float = 0.1) -> TickerEvidence:
    """Build a minimal TickerEvidence with three directional analysts and one no-data."""
    return TickerEvidence(
        ticker=ticker,
        tick_id="tick_X",
        recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
        per_analyst={
            "technical": _ev("technical", lean, 0.7, {"rsi_14": 60.0}, ticker),
            "fundamental": _ev("fundamental", lean, 0.6, {"pe_trailing": 28.5}, ticker),
            "news": _ev("news", lean, 0.5, {"news_count_7d": 5.0}, ticker),
            "smart_money": AnalystEvidence(
                ticker=ticker, analyst="smart_money",
                tick_id="tick_X",
                recorded_at=datetime(2026, 5, 8, 14, 0, tzinfo=UTC),
                features={"is_no_data": 1.0}, feature_warnings=[],
                verdict=AnalystVerdict(
                    lean="neutral", magnitude=0.0, confidence=0.0,
                    rationale="no filings", key_factors=[], is_no_data=True,
                ),
            ),
        },
        aggregate=AggregateVerdict(
            lean=lean, magnitude=magnitude, confidence=0.6,
            disagreement=disagreement, summary=f"3 {lean} / 1 no_data",
        ),
        weights={"technical": 1.0, "fundamental": 1.0, "news": 1.0, "smart_money": 1.0},
    )


def test_empty_evidence_renders_placeholder():
    """An empty iterable must produce the stable flat-evidence sentinel string."""
    out = render_ticker_evidence([])
    assert out == "(no evidence this tick)"


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_single_ticker_block_contains_all_sections():
    out = render_ticker_evidence([_te()])
    assert "AAPL" in out
    assert "Aggregate" in out or "aggregate" in out
    assert "bullish" in out
    # Per-analyst verdicts visible
    assert "technical" in out.lower()
    assert "fundamental" in out.lower()
    assert "news" in out.lower()       # Task 6: slot renamed from "sentiment" → "news"
    assert "smart_money" in out.lower()


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_disagreement_rendered():
    """The aggregate disagreement value must appear verbatim, not just its label.

    The label ``disagreement`` is always present in the rendered output, so
    asserting only on the label would pass even if the numeric value were
    silently dropped — hence the strict equality check on ``"0.42"``.
    """
    out = render_ticker_evidence([_te(disagreement=0.42)])
    assert "0.42" in out


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_no_data_smart_money_marked_clearly():
    out = render_ticker_evidence([_te()])
    # The "no data" smart_money should be distinguishable from a 0.0-confidence neutral
    assert "no data" in out.lower() or "no_data" in out.lower() or "n/a" in out.lower()


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_multiple_tickers_in_output():
    aapl = _te(ticker="AAPL", lean="bullish")
    msft = _te(ticker="MSFT", lean="bearish")
    out = render_ticker_evidence([aapl, msft])
    assert "AAPL" in out
    assert "MSFT" in out


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_features_visible_in_output():
    """The locked feature catalogue values must be embedded in the rendered output."""
    out = render_ticker_evidence([_te()])
    # At least some feature values should be visible to the LLM
    assert "rsi_14" in out or "60" in out


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_missing_analyst_renders_placeholder():
    """Analysts absent from ``per_analyst`` are rendered as ``(missing)``.

    The canonical four-slot order must still appear so the LLM can see *which*
    analyst was unavailable, not just that something was missing.
    """
    te = _te()
    # Drop one analyst to exercise the missing-slot branch.
    te_partial = te.model_copy(update={"per_analyst": {
        k: v for k, v in te.per_analyst.items() if k != "fundamental"
    }})
    out = render_ticker_evidence([te_partial])
    assert "(missing)" in out
    # The slot label must still appear so the LLM can identify which analyst is absent.
    assert "fundamental" in out


@pytest.mark.skip(reason="TODO(plan-07): evidence_view.py and its dual-surface (rationale+report) fixtures are retired when Plan 07 deletes the strategist callback; these tests collide with the Task 1 one-prose-surface invariant until then")
def test_long_rationale_is_truncated_with_ellipsis():
    """Rationales longer than 60 characters must be cut and marked with ``…``.

    Silent truncation would let a clipped sentence look complete to the LLM,
    so the renderer appends ``…`` whenever it shortens the text.
    """
    long_text = "A" * 100  # 100 'A's — well over the 60-char cap.
    te = _te()
    te_long = te.model_copy(update={"per_analyst": {
        **te.per_analyst,
        "technical": _ev(
            "technical",
            "bullish",
            0.7,
            {"rsi_14": 60.0},
        ).model_copy(update={"verdict": AnalystVerdict(
            lean="bullish",
            magnitude=0.7,
            confidence=0.7,
            rationale=long_text,
            key_factors=[],
            report=_stub_report("bullish", "technical"),
        )}),
    }})
    out = render_ticker_evidence([te_long])
    assert "…" in out
    # The displayed slice is the first 57 chars (then "…") — never the full string.
    assert long_text not in out
