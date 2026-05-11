"""TickerEvidence prompt rendering tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.evidence_view import render_ticker_evidence
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


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
            "sentiment": _ev("sentiment", lean, 0.5, {"news_count_7d": 5.0}, ticker),
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
        weights={"technical": 1.0, "fundamental": 1.0, "sentiment": 1.0, "smart_money": 1.0},
    )


def test_empty_evidence_renders_placeholder():
    out = render_ticker_evidence([])
    assert "no evidence" in out.lower() or "(no" in out.lower()


def test_single_ticker_block_contains_all_sections():
    out = render_ticker_evidence([_te()])
    assert "AAPL" in out
    assert "Aggregate" in out or "aggregate" in out
    assert "bullish" in out
    # Per-analyst verdicts visible
    assert "technical" in out.lower()
    assert "fundamental" in out.lower()
    assert "sentiment" in out.lower()
    assert "smart_money" in out.lower()


def test_disagreement_rendered():
    out = render_ticker_evidence([_te(disagreement=0.42)])
    assert "0.42" in out or "disagreement" in out.lower()


def test_no_data_smart_money_marked_clearly():
    out = render_ticker_evidence([_te()])
    # The "no data" smart_money should be distinguishable from a 0.0-confidence neutral
    assert "no data" in out.lower() or "no_data" in out.lower() or "n/a" in out.lower()


def test_multiple_tickers_in_output():
    aapl = _te(ticker="AAPL", lean="bullish")
    msft = _te(ticker="MSFT", lean="bearish")
    out = render_ticker_evidence([aapl, msft])
    assert "AAPL" in out
    assert "MSFT" in out


def test_features_visible_in_output():
    out = render_ticker_evidence([_te()])
    # At least some feature values should be visible to the LLM
    assert "rsi_14" in out or "60" in out
