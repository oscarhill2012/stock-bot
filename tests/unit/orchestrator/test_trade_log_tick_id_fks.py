"""TradeLogRow.opening_tick_id / closing_tick_id tests — Tier 1, no LLM."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, TickerStanceRow, TradeLogRow


@pytest.fixture
def session(tmp_path):
    """Yield a freshly-created SQLite session backed by a tmp file; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


def test_trade_log_accepts_tick_id_fks(session):
    """A TradeLogRow round-trips with both tick-id columns populated."""
    session.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="open_aapl", closed_tag="close_aapl",
        opened_rationale="x", close_reason="target",
        catalyst_realised=False,
        opening_tick_id="tick_OPEN", closing_tick_id="tick_CLOSE",
    ))
    session.commit()
    r = session.query(TradeLogRow).first()
    assert r.opening_tick_id == "tick_OPEN"
    assert r.closing_tick_id == "tick_CLOSE"


def test_trade_log_join_to_ticker_stance(session):
    """Closed-trade outcomes can be joined back to the deliberation that opened them."""
    session.add(TickerStanceRow(
        tick_id="tick_OPEN", recorded_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        ticker="AAPL", preferred_weight=0.08, conviction=0.7, rationale="x",
        horizon="swing", target_price=210.0, stop_price=185.0,
        catalyst=None, close_reason=None, trim_reason=None,
        lifecycle_action="open", decision_tag="open_aapl",
    ))
    session.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="open_aapl", closed_tag="close_aapl",
        opened_rationale="x", close_reason="target",
        catalyst_realised=False,
        opening_tick_id="tick_OPEN", closing_tick_id="tick_CLOSE",
    ))
    session.commit()
    joined = (
        session.query(TradeLogRow, TickerStanceRow)
        .filter(TradeLogRow.opening_tick_id == TickerStanceRow.tick_id)
        .filter(TradeLogRow.ticker == TickerStanceRow.ticker)
        .all()
    )
    assert len(joined) == 1
    trade, stance = joined[0]
    assert trade.ticker == "AAPL"
    assert stance.lifecycle_action == "open"


def test_tick_id_columns_nullable(session):
    """Old rows pre-Plan-C will have NULL tick IDs — must not break existing queries."""
    session.add(TradeLogRow(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        horizon_intent="swing",
        opened_tag="x", closed_tag="x",
        opened_rationale="x", close_reason="x",
        catalyst_realised=False,
        opening_tick_id=None, closing_tick_id=None,
    ))
    session.commit()
    r = session.query(TradeLogRow).first()
    assert r.opening_tick_id is None
    assert r.closing_tick_id is None
