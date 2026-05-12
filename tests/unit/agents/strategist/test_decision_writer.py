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
    decision = StrategistDecision(
        stances=[
            TickerStance(ticker="AAPL", preferred_weight=0.08, conviction=0.7,
                         rationale="open", horizon="swing",
                         target_price=210.0, stop_price=185.0),
            TickerStance(ticker="NVDA", preferred_weight=0.0, conviction=0.8,
                         rationale="exit", close_reason="thesis broken"),
        ],
        target_weights={"AAPL": 0.08, "NVDA": 0.0},
        decision_tag="rotation", reasoning="x", updated_thesis="y", confidence=0.65,
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
    assert by_ticker["AAPL"].lifecycle_action == "open"
    assert by_ticker["NVDA"].lifecycle_action == "close"
    assert all(r.decision_tag == "rotation" for r in rows)


def test_no_op_without_decision(session):
    state = {"tick_id": "t", "strategist_decision": None,
             "portfolio": Portfolio(cash=100.0).model_dump(mode="json")}
    writer = StrategistDecisionWriter(db_session=session)
    _run(writer._run_async_impl(_StubCtx(state)))
    session.commit()
    assert session.query(TickerStanceRow).count() == 0


def test_no_op_without_db_session():
    state = {
        "tick_id": "t",
        "strategist_decision": StrategistDecision(
            stances=[TickerStance(ticker="AAPL", preferred_weight=0.0,
                                  conviction=0.5, rationale="hold")],
            target_weights={"AAPL": 0.0},
            decision_tag="x", reasoning="x", updated_thesis="y", confidence=0.5,
        ).model_dump(mode="json"),
        "portfolio": Portfolio(cash=100.0).model_dump(mode="json"),
    }
    writer = StrategistDecisionWriter(db_session=None)
    _run(writer._run_async_impl(_StubCtx(state)))  # must not raise


def test_factory_returns_agent(session):
    agent = build_strategist_decision_writer(session)
    assert isinstance(agent, StrategistDecisionWriter)
    assert agent.db_session is session
