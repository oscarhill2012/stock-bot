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


# ── TradeLog ──────────────────────────────────────────────────────────

class TradeLogRow(Base):
    __tablename__ = "trade_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime)
    closed_at: Mapped[datetime] = mapped_column(DateTime)
    opened_price: Mapped[float] = mapped_column(Float)
    closed_price: Mapped[float] = mapped_column(Float)
    pnl_dollar: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    holding_period_hours: Mapped[int] = mapped_column(Integer)
    horizon_intent: Mapped[str] = mapped_column(String)
    opened_tag: Mapped[str] = mapped_column(String)
    closed_tag: Mapped[str] = mapped_column(String)
    opened_rationale: Mapped[str] = mapped_column(String)
    close_reason: Mapped[str] = mapped_column(String)
    catalyst_realised: Mapped[bool] = mapped_column(Boolean)


def save_trade_log_entry(session: Session, entry: dict) -> None:
    row = TradeLogRow(**entry)
    session.add(row)
    session.flush()


# ── PortfolioSnapshot ─────────────────────────────────────────────────

class PortfolioSnapshotRow(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    bot_total_value: Mapped[float] = mapped_column(Float)
    bot_cash: Mapped[float] = mapped_column(Float)
    bot_positions_value: Mapped[float] = mapped_column(Float)
    bot_position_count: Mapped[int] = mapped_column(Integer)
    spy_price: Mapped[float] = mapped_column(Float)
    spy_value_if_held: Mapped[float] = mapped_column(Float)
    bot_return_pct: Mapped[float] = mapped_column(Float)
    spy_return_pct: Mapped[float] = mapped_column(Float)
    excess_return_pct: Mapped[float] = mapped_column(Float)
    holdings_breakdown_json: Mapped[str] = mapped_column(String, default="{}")


def save_portfolio_snapshot(session: Session, snap: dict) -> None:
    import json as json_mod
    row = PortfolioSnapshotRow(
        tick_id=snap["tick_id"],
        recorded_at=snap.get("recorded_at", __import__("datetime").datetime.now(__import__("datetime").timezone.utc)),
        bot_total_value=snap["bot_total_value"],
        bot_cash=snap["bot_cash"],
        bot_positions_value=snap["bot_positions_value"],
        bot_position_count=snap["bot_position_count"],
        spy_price=snap["spy_price"],
        spy_value_if_held=snap["spy_value_if_held"],
        bot_return_pct=snap["bot_return_pct"],
        spy_return_pct=snap["spy_return_pct"],
        excess_return_pct=snap["excess_return_pct"],
        holdings_breakdown_json=json_mod.dumps(snap.get("holdings_breakdown", {})),
    )
    session.add(row)
    session.flush()


def make_engine(db_url: str = "sqlite://"):
    return create_engine(db_url)


def make_session_factory(engine):
    return sessionmaker(bind=engine)


def create_all(engine) -> None:
    Base.metadata.create_all(engine)


# ── ADK SessionService factory ────────────────────────────────────────

import os


def make_session_service():
    """Return a DatabaseSessionService configured by STOCKBOT_ENV.

    Dev: sqlite at ./data/stockbot.db (created on demand).
    Prod: DATABASE_URL env var (Postgres in deploy).
    """
    from google.adk.sessions import DatabaseSessionService

    env = os.environ.get("STOCKBOT_ENV", "dev").lower()
    if env == "prod":
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError(
                "STOCKBOT_ENV=prod requires DATABASE_URL to be set."
            )
        return DatabaseSessionService(db_url=url)

    # dev — aiosqlite driver required by DatabaseSessionService (uses async engine)
    from pathlib import Path
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)
    return DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{data_dir.absolute()}/stockbot.db")
