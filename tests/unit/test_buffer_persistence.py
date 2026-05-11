from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from orchestrator.persistence import Base, load_recent_buffer, save_buffer_entry


def _make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _entry_dict(tag: str = "hold") -> dict:
    return {
        "timestamp": datetime.now(tz=UTC),
        "decision_tag": tag,
        "reasoning_summary": "test reasoning",
        "smart_money_seen": False,
        "is_repeat": False,
        "executions_count": 2,
        "embedding": None,
    }


def test_round_trip_buffer_entry():
    session = _make_session()
    data = _entry_dict("buy_aapl")
    save_buffer_entry(session, data, tick_id="tick-001")
    session.commit()
    rows = load_recent_buffer(session, "tick-001")
    assert len(rows) == 1
    assert rows[0]["decision_tag"] == "buy_aapl"
    assert rows[0]["reasoning_summary"] == "test reasoning"


def test_load_respects_limit():
    session = _make_session()
    for i in range(5):
        save_buffer_entry(session, _entry_dict(f"tag_{i}"), tick_id="tick-001")
    session.commit()
    rows = load_recent_buffer(session, "tick-001", limit=3)
    assert len(rows) == 3
