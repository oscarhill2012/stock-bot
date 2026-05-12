from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from orchestrator.persistence import Base, PortfolioSnapshotRow, save_portfolio_snapshot


def _make_session():
    """Return an open SQLAlchemy Session backed by a fresh in-memory SQLite database."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(bind=engine)


def test_round_trip_portfolio_snapshot():
    session = _make_session()
    snap = {
        "tick_id": "tick-001",
        "recorded_at": datetime.now(tz=UTC),
        "bot_total_value": 10_500.0,
        "bot_cash": 1_000.0,
        "bot_positions_value": 9_500.0,
        "bot_position_count": 3,
        "spy_price": 470.0,
        "spy_value_if_held": 10_200.0,
        "bot_return_pct": 5.0,
        "spy_return_pct": 2.0,
        "excess_return_pct": 3.0,
        "holdings_breakdown": {"AAPL": 0.4, "MSFT": 0.3},
    }
    save_portfolio_snapshot(session, snap)
    session.commit()
    rows = session.query(PortfolioSnapshotRow).all()
    assert len(rows) == 1
    assert rows[0].bot_total_value == 10_500.0
    assert rows[0].excess_return_pct == 3.0
