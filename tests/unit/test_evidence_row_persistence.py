"""Unit-level round-trip persistence tests for AnalystEvidenceRow.

These tests verify that `save_analyst_evidence` correctly persists rows to an
in-memory SQLite database, focusing on the new Phase-5 analyst names ('news'
and 'social') introduced by the AnalystName literal expansion in Task 2.

A `sqlite_session` fixture is defined here so this file is self-contained and
can be extended by later tasks without pulling in the integration conftest.
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import AnalystEvidenceRow, Base, save_analyst_evidence


@pytest.fixture
def sqlite_session():
    """Yield an in-memory SQLite session with all tables created, then close."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


@pytest.mark.parametrize("analyst", ["news", "social"])
def test_evidence_row_round_trip_for_new_analyst_names(analyst, sqlite_session):
    """`news` and `social` analyst rows round-trip cleanly through the DB.

    Verifies that the plain String column on AnalystEvidenceRow accepts the new
    Phase-5 analyst names without requiring a schema migration.
    """
    save_analyst_evidence(
        sqlite_session,
        tick_id="tick-001",
        analyst=analyst,
        ticker="AAPL",
        verdict={
            "lean": "bullish",
            "magnitude": 0.5,
            "confidence": 0.6,
            "rationale": "round-trip",
            "key_factors": ["positive"],
            "is_no_data": False,
        },
        features={"score": 0.42},
    )
    sqlite_session.commit()

    rows = sqlite_session.query(AnalystEvidenceRow).filter_by(ticker="AAPL").all()
    assert any(r.analyst == analyst for r in rows), (
        f"Expected a row with analyst={analyst!r}; got {[r.analyst for r in rows]}"
    )
