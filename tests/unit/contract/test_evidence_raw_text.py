"""Schema guard — AnalystEvidence must carry an optional ``raw_text`` field.

The A2.5 brainstorm decision: the Strategist's prompt should be able to
include the raw provider text for News and Fundamental tickers, in
addition to the structured features + verdict.  Adds an optional
``raw_text: str | None = None`` field to ``AnalystEvidence``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from contract.evidence import AnalystEvidence, AnalystVerdict


def test_analyst_evidence_accepts_raw_text() -> None:
    """``raw_text`` must round-trip through model_validate / model_dump."""
    # is_no_data=True used here because this test exercises raw_text plumbing,
    # not LLM verdict content.  The D1.1 validator requires a report block
    # whenever is_no_data=False; using is_no_data=True keeps the fixture
    # focused on the schema field under test.
    ev = AnalystEvidence(
        analyst     = "news",
        ticker      = "AAPL",
        tick_id     = "t1",
        recorded_at = datetime(2026, 5, 20, tzinfo=UTC),
        features    = {},
        verdict     = AnalystVerdict(
            lean="neutral", magnitude=0.0, confidence=0.5, rationale="x",
            is_no_data=True,
        ),
        raw_text    = "Apple closes flat amid SVB contagion fears…",
    )
    dumped = ev.model_dump(mode="json")
    assert dumped["raw_text"].startswith("Apple closes flat")
    # Default None when omitted.
    ev2 = AnalystEvidence(
        analyst="news", ticker="MSFT", tick_id="t1",
        recorded_at=datetime(2026, 5, 20, tzinfo=UTC),
        features={},
        verdict=AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.5, rationale="x",
                               is_no_data=True),
    )
    assert ev2.raw_text is None
