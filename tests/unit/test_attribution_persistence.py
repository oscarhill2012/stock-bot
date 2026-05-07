# tests/unit/test_attribution_persistence.py
"""AttributionSignalsRow round-trip for all four analyst types."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.persistence import (
    AttributionSignalsRow,
    create_all,
    make_engine,
    make_session_factory,
    save_attribution_signal,
)


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    create_all(engine)
    SessionLocal = make_session_factory(engine)
    s = SessionLocal()
    yield s
    s.close()


def test_round_trip_technical(session):
    save_attribution_signal(
        session,
        tick_id="tick-1",
        analyst="technical",
        signal={
            "ticker": "AAPL", "direction": "bullish", "confidence": 0.7,
            "key_factors": ["MA crossover"],
        },
    )
    session.commit()
    row = session.query(AttributionSignalsRow).first()
    assert row.tick_id == "tick-1"
    assert row.analyst == "technical"
    assert row.ticker == "AAPL"
    assert row.direction == "bullish"
    assert row.confidence == 0.7


def test_round_trip_smart_money(session):
    save_attribution_signal(
        session,
        tick_id="tick-1",
        analyst="smart_money",
        signal={
            "ticker": "TSLA", "direction": "bullish", "conviction": "high",
            "insiders": ["Musk"], "politicians": [], "total_dollar_value": 50000.0,
        },
    )
    session.commit()
    row = session.query(AttributionSignalsRow).first()
    assert row.analyst == "smart_money"
    assert row.conviction == "high"
    assert row.total_dollar_value == 50000.0


def test_per_tick_count(session):
    for analyst in ("technical", "fundamental", "sentiment"):
        save_attribution_signal(
            session,
            tick_id="tick-1",
            analyst=analyst,
            signal={"ticker": "AAPL", "direction": "neutral", "confidence": 0.5,
                    "key_factors": []},
        )
    session.commit()
    assert session.query(AttributionSignalsRow).count() == 3
