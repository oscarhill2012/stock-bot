# tests/integration/test_attribution_writer.py
"""AttributionWriter persists every analyst signal in session.state to the DB."""
from __future__ import annotations

import pytest

from agents.attribution.writer import AttributionWriter
from orchestrator.persistence import (
    AttributionSignalsRow,
    create_all,
    make_engine,
    make_session_factory,
)


@pytest.fixture
def db_session():
    engine = make_engine("sqlite://")
    create_all(engine)
    SessionLocal = make_session_factory(engine)
    s = SessionLocal()
    yield s
    s.close()


class _StubCtx:
    def __init__(self, state):
        self.session = type("S", (), {"state": state})()


@pytest.mark.asyncio
async def test_writes_one_row_per_signal(db_session):
    state = {
        "tick_id": "tick-x",
        "technical_signals": [
            {"ticker": "AAPL", "direction": "bullish", "confidence": 0.6, "key_factors": []},
            {"ticker": "MSFT", "direction": "neutral", "confidence": 0.4, "key_factors": []},
        ],
        "fundamental_signals": [
            {"ticker": "AAPL", "direction": "bullish", "confidence": 0.7, "key_factors": []},
        ],
        "sentiment_signals": [
            {"ticker": "AAPL", "direction": "neutral", "confidence": 0.5,
             "key_factors": [], "top_headlines": ["x"], "social_score_delta": 0.0},
        ],
        "smart_money_signals": [
            {"ticker": "TSLA", "direction": "bullish", "conviction": "high",
             "insiders": ["X"], "politicians": [], "total_dollar_value": 1000.0},
        ],
    }
    writer = AttributionWriter(db_session=db_session)
    async for _ in writer._run_async_impl(_StubCtx(state)):
        pass
    db_session.commit()

    rows = db_session.query(AttributionSignalsRow).all()
    assert len(rows) == 5
    by_analyst = {r.analyst for r in rows}
    assert by_analyst == {"technical", "fundamental", "sentiment", "smart_money"}


@pytest.mark.asyncio
async def test_no_db_session_is_noop(caplog):
    writer = AttributionWriter(db_session=None)
    async for _ in writer._run_async_impl(_StubCtx({"tick_id": "t", "technical_signals": [
        {"ticker": "AAPL", "direction": "bullish", "confidence": 0.5, "key_factors": []}
    ]})):
        pass
    # No error; nothing to assert beyond "did not raise"
