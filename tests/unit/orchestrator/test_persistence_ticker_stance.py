"""TickerStanceRow tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, TickerStanceRow, save_ticker_stance


@pytest.fixture
def session(tmp_path):
    """Yield a freshly-created SQLite session backed by a tmp file; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_round_trip(session):
    """A fully-populated stance round-trips via save_ticker_stance → query."""
    save_ticker_stance(
        session, tick_id="tick_X", decision_tag="open_aapl",
        recorded_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        stance={
            "ticker": "AAPL", "preferred_weight": 0.08, "conviction": 0.7,
            "rationale": "FCF + insider", "horizon": "swing",
            "target_price": 210.0, "stop_price": 185.0,
            "catalyst": "Q3", "close_reason": None, "trim_reason": None,
        },
        lifecycle_action="open",
    )
    session.commit()
    rows = session.query(TickerStanceRow).all()
    assert len(rows) == 1
    r = rows[0]
    assert r.tick_id == "tick_X"
    assert r.ticker == "AAPL"
    assert r.preferred_weight == 0.08
    assert r.lifecycle_action == "open"
    assert r.decision_tag == "open_aapl"


def test_nullable_lifecycle_fields(session):
    """A hold stance leaves horizon / target / stop / catalyst / close / trim as NULL."""
    save_ticker_stance(
        session, tick_id="tick_X", decision_tag="hold_msft",
        recorded_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        stance={
            "ticker": "MSFT", "preferred_weight": 0.05, "conviction": 0.6,
            "rationale": "still cheap", "horizon": None,
            "target_price": None, "stop_price": None,
            "catalyst": None, "close_reason": None, "trim_reason": None,
        },
        lifecycle_action="hold",
    )
    session.commit()
    r = session.query(TickerStanceRow).first()
    assert r.horizon is None
    assert r.target_price is None
    assert r.stop_price is None
    assert r.catalyst is None
    assert r.close_reason is None
    assert r.trim_reason is None
    assert r.lifecycle_action == "hold"
