"""Tests that writer-side agents honour state["as_of"] instead of wall-clock.

Each test sets state["as_of"] to a fixed past datetime and asserts that the
recorded_at field written to the database (or the entry timestamp) equals that
datetime rather than the current time.  This validates spec §architecture-
constraint #4: wall-clock leakage outside the fetch path is closed so that
backtest replay is fully deterministic.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import (
    Base,
    AnalystEvidenceRow,
    TickerEvidenceRow,
    TickerStanceRow,
    PortfolioSnapshotRow,
    TradeLogRow,
)

# ── Fixed historical timestamp used as state["as_of"] in every test ──────────

_HISTORICAL_TS = datetime(2023, 3, 10, 9, 30, 0, tzinfo=UTC)


# ── Shared helpers ────────────────────────────────────────────────────────────

class _StubCtx:
    """Minimal ADK InvocationContext stand-in — only exposes session.state.

    Also carries a real string ``invocation_id`` because Snapshotter / Executor
    / MemoryWriter now yield an ``Event`` whose ``invocation_id`` field is
    Pydantic-validated as ``str`` (state_delta cross-tick propagation, see
    ``docs/todo-fixes.md`` Group 2.5).
    """

    def __init__(self, state: dict) -> None:
        class _S:
            pass
        self.session = _S()
        self.session.state = state
        self.invocation_id = "test-invocation"


def _run(coro_gen) -> list:
    """Drain an async generator synchronously and return collected items."""
    async def _drain():
        return [ev async for ev in coro_gen]
    return asyncio.run(_drain())


@pytest.fixture()
def db_session(tmp_path):
    """Yield a fresh in-memory SQLite session; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()


# ── StrategistDecisionWriter ──────────────────────────────────────────────────

def test_decision_writer_uses_as_of(db_session) -> None:
    """StrategistDecisionWriter should stamp recorded_at from state["as_of"]."""
    from agents.strategist.decision_writer import StrategistDecisionWriter
    from agents.strategist.schema import StrategistDecision
    from agents.strategist.stance_schema import TickerStance
    from broker.portfolio import Portfolio

    decision = StrategistDecision(
        stances=[
            TickerStance(
                ticker="AAPL",
                preferred_weight=0.0,
                conviction=0.5,
                rationale="hold",
            ),
        ],
        target_weights={"AAPL": 0.0},
        decision_tag="test_tag",
        reasoning="x",
        thesis="y",
        confidence=0.5,
    )
    state = {
        "tick_id": "tick_1",
        "as_of": _HISTORICAL_TS,
        "strategist_decision": decision.model_dump(mode="json"),
        "portfolio": Portfolio(cash=1000.0).model_dump(mode="json"),
    }

    writer = StrategistDecisionWriter(db_session=db_session)
    _run(writer._run_async_impl(_StubCtx(state)))
    db_session.commit()

    row = db_session.query(TickerStanceRow).one()
    # SQLite's DateTime column strips tzinfo on round-trip; compare naive UTC values.
    assert row.recorded_at == _HISTORICAL_TS.replace(tzinfo=None)


# ── SnapshotterAgent ──────────────────────────────────────────────────────────

def test_snapshotter_uses_as_of(db_session) -> None:
    """SnapshotterAgent should stamp portfolio snapshots with state["as_of"]."""
    from unittest.mock import AsyncMock, MagicMock
    from agents.snapshot.agent import SnapshotterAgent
    from broker.portfolio import Portfolio

    # Fake broker returns a trivial portfolio.
    portfolio = Portfolio(cash=10_000.0)
    mock_broker = MagicMock()
    mock_broker.get_portfolio = AsyncMock(return_value=portfolio)

    state = {
        "tick_id": "tick_snap",
        "as_of": _HISTORICAL_TS,
    }

    agent = SnapshotterAgent(broker=mock_broker, db_session=db_session)

    # Patch yfinance so the agent doesn't hit the network.
    import sys
    import types
    fake_yf = types.ModuleType("yfinance")
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = MagicMock(empty=True)
    fake_yf.Ticker = MagicMock(return_value=fake_ticker)
    sys.modules["yfinance"] = fake_yf

    _run(agent._run_async_impl(_StubCtx(state)))
    db_session.commit()

    row = db_session.query(PortfolioSnapshotRow).one()
    # SQLite's DateTime column strips tzinfo on round-trip; compare naive UTC values.
    assert row.recorded_at == _HISTORICAL_TS.replace(tzinfo=None)


# ── EvidenceWriter ────────────────────────────────────────────────────────────

def test_evidence_writer_uses_as_of(db_session) -> None:
    """EvidenceWriter should stamp AnalystEvidenceRow and TickerEvidenceRow with as_of."""
    from agents.contract.evidence_writer import EvidenceWriter

    verdict = {
        "lean": "bullish",
        "magnitude": 0.6,
        "confidence": 0.7,
        "rationale": "test",
        "key_factors": [],
        "is_no_data": False,
    }
    tech_ev = {
        "ticker": "AAPL",
        "analyst": "technical",
        "verdict": verdict,
        "features": {},
        "feature_warnings": [],
    }
    ticker_ev_obj = {
        "ticker": "AAPL",
        "aggregate": {
            "lean": "bullish",
            "magnitude": 0.6,
            "confidence": 0.7,
            "disagreement": 0.0,
            "summary": "",
        },
        "weights": {"technical": 1.0},
        "per_analyst": {"technical": verdict},
    }

    state = {
        "tick_id": "tick_ev",
        "as_of": _HISTORICAL_TS,
        "technical_evidence": [tech_ev],
        # A2.6: EvidenceWriter reads from the temp:-prefixed key.
        "temp:ticker_evidence_objects": [ticker_ev_obj],
    }

    writer = EvidenceWriter(db_session=db_session)
    _run(writer._run_async_impl(_StubCtx(state)))
    # commit already called inside _run_async_impl

    analyst_row = db_session.query(AnalystEvidenceRow).one()
    ticker_row  = db_session.query(TickerEvidenceRow).one()

    # SQLite's DateTime column strips tzinfo on round-trip; compare naive UTC values.
    naive_ts = _HISTORICAL_TS.replace(tzinfo=None)
    assert analyst_row.recorded_at == naive_ts
    assert ticker_row.recorded_at  == naive_ts


# ── ExecutorAgent: closed_at ──────────────────────────────────────────────────

def test_executor_closed_at_uses_as_of(db_session) -> None:
    """ExecutorAgent should stamp closed_at from state["as_of"] for deterministic holding_hours."""
    from unittest.mock import AsyncMock, MagicMock
    from agents.executor.agent import ExecutorAgent
    from broker.fake import FakeBroker
    from orchestrator.state import Order

    # Opened 24 hours before the historical tick — holding_hours should be 24.
    from datetime import timedelta
    opened_at = _HISTORICAL_TS - timedelta(hours=24)
    opened_at_str = opened_at.isoformat()

    broker = FakeBroker(starting_cash=10_000, prices={"AAPL": 155.0})
    # Pre-populate broker position so the SELL order isn't rejected.
    asyncio.run(broker.submit_market("AAPL", "BUY", 10))

    thesis = {
        "opened_at": opened_at_str,
        "opened_price": 150.0,
        "opened_tag": "open_tag",
        "rationale": "was bullish",
        "horizon": "swing",
        "opened_tick_id": "tick_open",
    }

    order = Order(ticker="AAPL", action="SELL", quantity=10, est_price=155.0)
    state = {
        "tick_id": "tick_close",
        "as_of": _HISTORICAL_TS,
        "final_orders": [order.model_dump()],
        "positions": {"AAPL": thesis},
        "strategist_decision": {
            "decision_tag": "close_tag",
            "close_reasons": {"AAPL": "thesis expired"},
        },
    }

    agent = ExecutorAgent(broker=broker, db_session=db_session)
    _run(agent._run_async_impl(_StubCtx(state)))
    db_session.commit()

    row = db_session.query(TradeLogRow).one()
    # closed_at must equal the historical tick timestamp.
    # SQLite DateTime strips tzinfo on round-trip — compare naive UTC values.
    assert row.closed_at == _HISTORICAL_TS.replace(tzinfo=None)
    # holding_hours must be deterministic (24 h) rather than wall-clock-derived.
    assert row.holding_period_hours == 24


# ── MemoryWriter ──────────────────────────────────────────────────────────────

def test_memory_writer_uses_as_of() -> None:
    """MemoryWriter should stamp BufferEntry.timestamp from state["as_of"]."""
    from agents.memory.writer import MemoryWriter

    decision = {
        "decision_tag": "hold_all",
        "reasoning": "no clear catalyst",
        "thesis": "neutral",
    }
    state = {
        "as_of": _HISTORICAL_TS,
        "strategist_decision": decision,
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
    }

    writer = MemoryWriter()
    _run(writer._run_async_impl(_StubCtx(state)))

    # The updated buffer is written back to state as a list of dicts.
    buffer = state.get("memory_buffer", [])
    assert len(buffer) == 1, "Expected one entry in the memory buffer"
    entry_ts = buffer[0]["timestamp"]

    # Normalise: may be datetime or ISO string depending on model_dump mode.
    if isinstance(entry_ts, str):
        entry_ts = datetime.fromisoformat(entry_ts)

    assert entry_ts == _HISTORICAL_TS
