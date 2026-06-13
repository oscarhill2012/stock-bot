"""EvidenceWriter persists analyst + ticker evidence from session state."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.contract.evidence_writer import EvidenceWriter, build_evidence_writer
from orchestrator.persistence import (
    AnalystEvidenceRow,
    Base,
    TickerEvidenceRow,
)


@pytest.fixture
def db_session():
    """In-memory SQLite session with all StockBot tables pre-created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine)
    yield session
    session.close()


def _evidence(analyst, ticker, lean="bullish"):
    """Build a minimal AnalystEvidence dict for use in session state."""
    return {
        "analyst": analyst,
        "ticker": ticker,
        "tick_id": "2026-05-08T14:00:00Z",
        "recorded_at": "2026-05-08T14:00:00Z",
        "verdict": {
            "lean": lean,
            "magnitude": 0.5,
            "confidence": 0.6,
            "rationale": f"{analyst} rationale",
            "key_factors": [f"{analyst} factor"],
            "is_no_data": False,
        },
        "features": {f"{analyst}_feature": 1.0},
    }


def _ticker_evidence(ticker):
    """Build a minimal TickerEvidence dict for use in session state."""
    return {
        "ticker": ticker,
        "tick_id": "2026-05-08T14:00:00Z",
        "recorded_at": "2026-05-08T14:00:00Z",
        "per_analyst": {
            "technical": _evidence("technical", ticker),
            "fundamental": _evidence("fundamental", ticker),
        },
        "aggregate": {
            "lean": "bullish",
            "magnitude": 0.45,
            "confidence": 0.6,
            "disagreement": 0.1,
            "summary": "2/2 bullish",
        },
        "weights": {"technical": 1.0, "fundamental": 1.0, "news": 1.0, "smart_money": 1.0},
    }


@pytest.mark.asyncio
async def test_evidence_writer_persists_both_row_types(db_session):
    """Writer must write one AnalystEvidenceRow per analyst and one TickerEvidenceRow per ticker."""
    writer = EvidenceWriter(db_session=db_session)
    state = {
        "tick_id": "2026-05-08T14:00:00Z",
        "technical_evidence": [_evidence("technical", "AAPL")],
        "fundamental_evidence": [_evidence("fundamental", "AAPL")],
        "news_evidence": [],
        "smart_money_evidence": [],
        # A2.6: EvidenceWriter reads from the temp:-prefixed key.
        "temp:ticker_evidence_objects": [_ticker_evidence("AAPL")],
    }
    ctx = MagicMock()
    ctx.session.state = state
    async for _ in writer._run_async_impl(ctx):
        pass

    analyst_rows = db_session.query(AnalystEvidenceRow).all()
    assert len(analyst_rows) == 2
    assert {r.analyst for r in analyst_rows} == {"technical", "fundamental"}

    ticker_rows = db_session.query(TickerEvidenceRow).all()
    assert len(ticker_rows) == 1
    assert ticker_rows[0].ticker == "AAPL"
    assert ticker_rows[0].analyst_count == 2


@pytest.mark.asyncio
async def test_evidence_writer_no_db_is_noop():
    """Writer with db_session=None must short-circuit before touching state and yield nothing."""
    writer = EvidenceWriter(db_session=None)
    ctx = MagicMock()
    # Provide a state object so we can prove the short-circuit fires *before* it is touched.
    ctx.session.state = MagicMock()
    events = [e async for e in writer._run_async_impl(ctx)]
    assert events == []
    # The early `if self.db_session is None: return` must fire before any state read.
    ctx.session.state.__getitem__.assert_not_called()
    ctx.session.state.get.assert_not_called()


def test_factory_returns_named_agent():
    """build_evidence_writer must produce an EvidenceWriter with the correct name."""
    w = build_evidence_writer(db_session=None)
    assert w.name == "EvidenceWriter"


@pytest.mark.asyncio
async def test_evidence_writer_accepts_iso_string_as_of(db_session):
    """state["as_of"] may arrive as an ISO-8601 string after DatabaseSessionService
    JSON serialisation; the writer must accept it without raising AsOfRequiredError.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    writer = EvidenceWriter(db_session=db_session)
    iso_as_of = "2026-05-08T14:00:00+00:00"

    state = {
        "tick_id":                       "2026-05-08T14:00:00Z",
        "as_of":                         iso_as_of,          # ISO string, not datetime
        "technical_evidence":            [_evidence("technical", "AAPL")],
        "fundamental_evidence":          [],
        "news_evidence":                 [],
        "smart_money_evidence":          [],
        "temp:ticker_evidence_objects":  [_ticker_evidence("AAPL")],
    }
    ctx = MagicMock()
    ctx.session.state = state
    async for _ in writer._run_async_impl(ctx):
        pass

    rows = db_session.query(AnalystEvidenceRow).all()
    assert len(rows) == 1
    # The recorded_at on the row must represent the same moment as the ISO string.
    # SQLite strips timezone info when storing, so compare naive datetimes.
    expected_dt = datetime.fromisoformat(iso_as_of).replace(tzinfo=None)
    assert rows[0].recorded_at == expected_dt
