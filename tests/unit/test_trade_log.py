from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import Base, TradeLogRow, save_trade_log_entry


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(bind=engine)


def test_round_trip_trade_log_entry():
    session = _make_session()
    now = datetime.now(tz=UTC)
    entry = {
        "ticker": "AAPL",
        "opened_at": now - timedelta(hours=4),
        "closed_at": now,
        "opened_price": 195.0,
        "closed_price": 200.0,
        "pnl_dollar": 50.0,
        "pnl_pct": 2.56,
        "holding_period_hours": 4,
        # ``horizon_intent`` removed in iter-3 — dropped from TradeLogRow.
        "opened_tag": "breakout_buy",
        "closed_tag": "profit_target_hit",
        "opened_rationale": "Technical breakout",
        "close_reason": "Target reached",
        "catalyst_realised": True,
    }
    save_trade_log_entry(session, entry)
    session.commit()
    rows = session.query(TradeLogRow).all()
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].catalyst_realised is True
