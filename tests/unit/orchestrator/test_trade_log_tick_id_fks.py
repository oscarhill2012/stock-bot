"""TradeLogRow.opening_tick_id / closing_tick_id tests — Tier 1, no LLM.

Updated for iter-3: horizon_intent dropped from TradeLogRow; horizon /
target_price / stop_price dropped from TickerStanceRow.  lifecycle_action
now uses the three-verb vocabulary (buy / sell / update).
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import Base, TickerStanceRow, TradeLogRow


@pytest.fixture
def session(tmp_path):
    """Yield a freshly-created SQLite session backed by a tmp file; close on teardown."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    with Session(bind=engine) as s:
        yield s


def _make_trade_log_row(**kwargs) -> TradeLogRow:
    """Build a minimal ``TradeLogRow`` with sensible defaults.

    ``horizon_intent`` is intentionally absent — it was removed in iter-3.
    """
    defaults = dict(
        ticker="AAPL",
        opened_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        closed_at=datetime(2026, 5, 8, 14, tzinfo=UTC),
        opened_price=192.40, closed_price=210.0,
        pnl_dollar=88.0, pnl_pct=9.13,
        holding_period_hours=504,
        opened_tag="buy_aapl", closed_tag="close_aapl",
        opened_rationale="x", close_reason="target",
        catalyst_realised=False,
        opening_tick_id=None, closing_tick_id=None,
    )
    defaults.update(kwargs)
    return TradeLogRow(**defaults)


def test_trade_log_accepts_tick_id_fks(session):
    """A TradeLogRow round-trips with both tick-id columns populated."""
    session.add(_make_trade_log_row(
        opening_tick_id="tick_OPEN",
        closing_tick_id="tick_CLOSE",
    ))
    session.commit()
    r = session.query(TradeLogRow).first()
    assert r.opening_tick_id == "tick_OPEN"
    assert r.closing_tick_id == "tick_CLOSE"
    # iter-3: horizon_intent column was dropped.
    assert not hasattr(r, "horizon_intent")


def test_trade_log_join_to_ticker_stance(session):
    """Closed-trade outcomes can be joined back to the deliberation that opened them."""
    # TickerStanceRow: iter-3 schema — no horizon / target_price / stop_price /
    # close_reason / trim_reason.
    session.add(TickerStanceRow(
        tick_id="tick_OPEN", recorded_at=datetime(2026, 4, 1, 14, tzinfo=UTC),
        ticker="AAPL", preferred_weight=0.05, conviction=0.7, rationale="x",
        catalyst=None,
        lifecycle_action="buy", decision_tag="buy_aapl",
    ))
    session.add(_make_trade_log_row(
        opening_tick_id="tick_OPEN",
        closing_tick_id="tick_CLOSE",
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
    assert stance.lifecycle_action == "buy"


def test_tick_id_columns_nullable(session):
    """Old rows pre-Plan-C will have NULL tick IDs — must not break existing queries."""
    session.add(_make_trade_log_row(
        opening_tick_id=None,
        closing_tick_id=None,
    ))
    session.commit()
    r = session.query(TradeLogRow).first()
    assert r.opening_tick_id is None
    assert r.closing_tick_id is None
