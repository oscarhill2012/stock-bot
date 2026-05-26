"""StrategistDecisionWriter tests — Tier 1, no LLM."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agents.strategist.decision_writer import (
    StrategistDecisionWriter,
    build_strategist_decision_writer,
)
from agents.strategist.schema import StrategistDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio, Position
from orchestrator.persistence import Base, TickerStanceRow


class _StubCtx:
    """Minimal stand-in for ADK InvocationContext — only exposes session.state."""
    def __init__(self, state: dict):
        class _S:
            pass
        self.session = _S()
        self.session.state = state


def _run(coro_gen):
    """Drain an async generator to a list synchronously."""
    async def _drain():
        return [ev async for ev in coro_gen]
    return asyncio.run(_drain())


@pytest.fixture
def session(tmp_path):
    """Yield a freshly-created SQLite session backed by a tmp file; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    s = Session(bind=engine)
    yield s
    s.close()


def test_writes_one_row_per_stance(session):
    """One row per stance is written; lifecycle_action is set from intent."""
    decision = StrategistDecision(
        stances=[
            TickerStance(
                ticker="AAPL",
                intent="buy",
                weight=0.04,
                rationale="FCF-driven thesis",
            ),
            TickerStance(
                ticker="NVDA",
                intent="sell",
                rationale="thesis broken",
            ),
        ],
        target_weights={"AAPL": 0.04, "NVDA": 0.0},
        decision_tag="rotation", reasoning="x", thesis="y", confidence=0.65,
    )
    portfolio = Portfolio(
        cash=900.0,
        positions={"NVDA": Position(quantity=1.0, avg_cost=900.0, last_price=850.0)},
    )
    state = {
        "tick_id": "tick_X",
        "strategist_decision": decision.model_dump(mode="json"),
        "portfolio": portfolio.model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=session)
    _run(writer._run_async_impl(_StubCtx(state)))
    session.commit()

    rows = session.query(TickerStanceRow).all()
    assert len(rows) == 2
    by_ticker = {r.ticker: r for r in rows}
    assert by_ticker["AAPL"].lifecycle_action == "buy"
    assert by_ticker["NVDA"].lifecycle_action == "sell"
    assert all(r.decision_tag == "rotation" for r in rows)


def test_no_op_without_decision(session):
    state = {"tick_id": "t", "strategist_decision": None,
             "portfolio": Portfolio(cash=100.0).model_dump(mode="json")}
    writer = StrategistDecisionWriter(db_session=session)
    _run(writer._run_async_impl(_StubCtx(state)))
    session.commit()
    assert session.query(TickerStanceRow).count() == 0


def test_no_op_without_db_session():
    """Writer with db_session=None must not raise — write is a no-op."""
    state = {
        "tick_id": "t",
        "strategist_decision": StrategistDecision(
            stances=[TickerStance(ticker="AAPL", intent="update", rationale="test update")],
            target_weights={"AAPL": 0.0},
            decision_tag="x", reasoning="x", thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
        "portfolio": Portfolio(cash=100.0).model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=None)
    _run(writer._run_async_impl(_StubCtx(state)))  # must not raise


def test_factory_returns_agent(session):
    agent = build_strategist_decision_writer(session)
    assert isinstance(agent, StrategistDecisionWriter)
    assert agent.db_session is session


def test_accepts_iso_string_as_of(session):
    """state["as_of"] arriving as an ISO-8601 string (from DatabaseSessionService
    JSON round-trip) must not raise; recorded_at on the DB row must be correct.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    iso_as_of = "2026-05-08T14:00:00+00:00"
    decision = StrategistDecision(
        stances=[
            TickerStance(
                ticker="AAPL",
                intent="buy",
                weight=0.05,
                rationale="Strong FCF-driven thesis",
            ),
        ],
        target_weights={"AAPL": 0.05},
        decision_tag="iso_as_of_test", reasoning="x", thesis="y",
        confidence=0.6,
    )
    portfolio = Portfolio(cash=1000.0)
    state = {
        "tick_id":              "t-iso",
        "as_of":                iso_as_of,      # ISO string, not datetime
        "strategist_decision":  decision.model_dump(mode="json"),
        "portfolio":            portfolio.model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=session)
    _run(writer._run_async_impl(_StubCtx(state)))
    session.commit()

    rows = session.query(TickerStanceRow).all()
    assert len(rows) == 1
    # SQLite strips timezone info when storing; compare naive datetimes.
    expected_dt = datetime.fromisoformat(iso_as_of).replace(tzinfo=None)
    assert rows[0].recorded_at == expected_dt
