"""SQL persistence layer. SQLAlchemy ORM for all durable state."""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class BufferEntryRow(Base):
    __tablename__ = "buffer_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime)
    decision_tag: Mapped[str] = mapped_column(String)
    reasoning_summary: Mapped[str] = mapped_column(String)
    smart_money_seen: Mapped[bool] = mapped_column(Boolean)
    is_repeat: Mapped[bool] = mapped_column(Boolean)
    executions_count: Mapped[int] = mapped_column(Integer)
    embedding_json: Mapped[str | None] = mapped_column(String, nullable=True)


def save_buffer_entry(session: Session, entry_data: dict, tick_id: str) -> None:
    from agents.memory.schema import BufferEntry
    entry = BufferEntry.model_validate(entry_data)
    row = BufferEntryRow(
        tick_id=tick_id,
        timestamp=entry.timestamp,
        decision_tag=entry.decision_tag,
        reasoning_summary=entry.reasoning_summary,
        smart_money_seen=entry.smart_money_seen,
        is_repeat=entry.is_repeat,
        executions_count=entry.executions_count,
        embedding_json=json.dumps(entry.embedding) if entry.embedding else None,
    )
    session.add(row)
    session.flush()


def load_recent_buffer(session: Session, tick_id: str, limit: int = 24) -> list[dict]:
    from agents.memory.schema import BufferEntry
    rows = (
        session.query(BufferEntryRow)
        .filter(BufferEntryRow.tick_id == tick_id)
        .order_by(BufferEntryRow.id.desc())
        .limit(limit)
        .all()
    )
    result = []
    for row in reversed(rows):
        result.append({
            "timestamp": row.timestamp,
            "decision_tag": row.decision_tag,
            "reasoning_summary": row.reasoning_summary,
            "smart_money_seen": row.smart_money_seen,
            "is_repeat": row.is_repeat,
            "executions_count": row.executions_count,
            "embedding": json.loads(row.embedding_json) if row.embedding_json else None,
        })
    return result


def make_engine(db_url: str = "sqlite://"):
    return create_engine(db_url)


def make_session_factory(engine):
    return sessionmaker(bind=engine)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)
