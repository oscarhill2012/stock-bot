"""AnalystEvidenceRow + TickerEvidenceRow round-trip."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import (
    AnalystEvidenceRow,
    Base,
    TickerEvidenceRow,
    save_analyst_evidence,
    save_ticker_evidence,
)


@pytest.fixture
def db_session():
    """Yield an in-memory SQLite session with all tables created, then close."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def test_save_analyst_evidence_round_trip(db_session):
    """Saving an analyst evidence dict should produce a correctly mapped row."""
    save_analyst_evidence(
        db_session,
        tick_id="2026-05-08T14:00:00Z",
        analyst="technical",
        ticker="AAPL",
        verdict={
            "lean": "bullish",
            "magnitude": 0.6,
            "confidence": 0.7,
            "rationale": "uptrend with low volatility",
            "key_factors": ["rsi_14: 62"],
            "is_no_data": False,
        },
        features={"rsi_14": 62.0, "atr_pct_14": 0.018},
    )
    db_session.commit()
    rows = db_session.query(AnalystEvidenceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.tick_id == "2026-05-08T14:00:00Z"
    assert r.analyst == "technical"
    assert r.ticker == "AAPL"
    assert r.lean == "bullish"
    assert r.magnitude == pytest.approx(0.6)
    assert r.confidence == pytest.approx(0.7)
    assert r.is_no_data is False
    assert json.loads(r.features_json) == {"rsi_14": 62.0, "atr_pct_14": 0.018}
    assert json.loads(r.key_factors_json) == ["rsi_14: 62"]
    assert r.rationale == "uptrend with low volatility"
    assert r.id is not None


def test_save_ticker_evidence_round_trip(db_session):
    """Saving a ticker evidence dict should produce a correctly mapped row."""
    save_ticker_evidence(
        db_session,
        tick_id="2026-05-08T14:00:00Z",
        ticker="AAPL",
        aggregate={
            "lean": "bullish",
            "magnitude": 0.45,
            "confidence": 0.6,
            "disagreement": 0.12,
            "summary": "3/4 analysts bullish with low disagreement",
        },
        weights={"technical": 1.0, "fundamental": 1.0, "news": 1.0, "smart_money": 1.0},
        analyst_count=4,
    )
    db_session.commit()
    rows = db_session.query(TickerEvidenceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.ticker == "AAPL"
    assert r.lean == "bullish"
    assert r.tick_id == "2026-05-08T14:00:00Z"
    assert r.magnitude == pytest.approx(0.45)
    assert r.confidence == pytest.approx(0.6)
    assert r.disagreement == pytest.approx(0.12)
    assert r.analyst_count == 4
    assert r.summary == "3/4 analysts bullish with low disagreement"
    assert r.id is not None
    assert json.loads(r.weights_json) == {
        "technical": 1.0,
        "fundamental": 1.0,
        "news": 1.0,
        "smart_money": 1.0,
    }
