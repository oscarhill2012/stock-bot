"""SQL persistence layer. SQLAlchemy ORM for all durable state."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, create_engine
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
    """Persist one memory buffer entry. `session.flush()` is called; caller commits."""
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
    """Return the `limit` most-recent buffer entries for `tick_id`, oldest first."""
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

    # Nullable FK-style references linking a trade back to the originating tick.
    # Populated by the executor when opening/closing a position; NULL for pre-Plan-C rows.
    opening_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    closing_tick_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)


def save_trade_log_entry(session: Session, entry: dict) -> None:
    """Persist one closed-trade record. Caller is responsible for committing."""
    row = TradeLogRow(**entry)
    session.add(row)
    session.flush()


# ── TickerStanceRow ──────────────────────────────────────────────────

class TickerStanceRow(Base):
    """One row per ticker per tick — strategist's per-ticker decision substrate."""

    __tablename__ = "ticker_stances"

    id: Mapped[int]                    = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]               = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]      = mapped_column(DateTime)
    ticker: Mapped[str]                = mapped_column(String, index=True)
    preferred_weight: Mapped[float]    = mapped_column(Float)
    conviction: Mapped[float]          = mapped_column(Float)
    rationale: Mapped[str]             = mapped_column(String)
    horizon: Mapped[str | None]        = mapped_column(String, nullable=True)
    target_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_price: Mapped[float | None]   = mapped_column(Float, nullable=True)
    catalyst: Mapped[str | None]       = mapped_column(String, nullable=True)
    close_reason: Mapped[str | None]   = mapped_column(String, nullable=True)
    trim_reason: Mapped[str | None]    = mapped_column(String, nullable=True)
    lifecycle_action: Mapped[str]      = mapped_column(String, index=True)
    decision_tag: Mapped[str]          = mapped_column(String, index=True)


def save_ticker_stance(
    session: Session,
    *,
    tick_id: str,
    decision_tag: str,
    recorded_at: datetime,
    stance: dict,
    lifecycle_action: str,
) -> None:
    """Persist one ticker stance row. The caller is responsible for committing.

    Args:
        session: SQLAlchemy session used for the insert.
        tick_id: Identifier of the tick that produced this stance.
        decision_tag: Snake_case label for the tick (mirrors
            ``StrategistDecision.decision_tag``).
        recorded_at: Wall-clock time the strategist produced the decision
            (timezone-aware).
        stance: Dump of a ``TickerStance`` (a dict produced by
            ``TickerStance.model_dump(mode="json")``). Must contain ``ticker``,
            ``preferred_weight``, ``conviction`` and ``rationale``; remaining
            lifecycle fields may be missing or ``None``.
        lifecycle_action: One of ``"open" | "close" | "trim" | "add" | "hold"``
            — the derived action this stance represents, computed by
            ``derive_lifecycle_action`` and saved alongside the stance so that
            downstream analytics can filter without recomputing.

    Returns:
        None. The new row is added and flushed but **not** committed; the caller
        controls commit ordering so that the stance write can be batched with
        other writes for the same tick.
    """
    row = TickerStanceRow(
        tick_id=tick_id,
        recorded_at=recorded_at,
        ticker=stance["ticker"],
        preferred_weight=stance["preferred_weight"],
        conviction=stance["conviction"],
        rationale=stance["rationale"],
        horizon=stance.get("horizon"),
        target_price=stance.get("target_price"),
        stop_price=stance.get("stop_price"),
        catalyst=stance.get("catalyst"),
        close_reason=stance.get("close_reason"),
        trim_reason=stance.get("trim_reason"),
        lifecycle_action=lifecycle_action,
        decision_tag=decision_tag,
    )
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
    """Persist one equity-curve data point. Caller is responsible for committing."""
    import json as json_mod
    row = PortfolioSnapshotRow(
        tick_id=snap["tick_id"],
        recorded_at=snap.get("recorded_at", datetime.now(tz=UTC)),
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


# ── AttributionSignals ────────────────────────────────────────────────

class AttributionSignalsRow(Base):
    """One row per analyst signal per tick. `analyst` discriminates type-specific columns."""

    __tablename__ = "attribution_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)
    direction: Mapped[str] = mapped_column(String)

    # Dense-analyst fields (NULL for smart_money rows)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    key_factors_json: Mapped[str] = mapped_column(String, default="[]")

    # Sentiment-only
    top_headlines_json: Mapped[str | None] = mapped_column(String, nullable=True)
    social_score_delta: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Smart-money-only
    conviction: Mapped[str | None] = mapped_column(String, nullable=True)
    insiders_json: Mapped[str | None] = mapped_column(String, nullable=True)
    politicians_json: Mapped[str | None] = mapped_column(String, nullable=True)
    total_dollar_value: Mapped[float | None] = mapped_column(Float, nullable=True)


def save_attribution_signal(
    session: Session,
    *,
    tick_id: str,
    analyst: str,
    signal: dict,
) -> None:
    """Persist one analyst signal. `analyst` must be technical|fundamental|sentiment|smart_money."""
    now = datetime.now(tz=UTC)
    common = dict(
        tick_id=tick_id,
        recorded_at=now,
        analyst=analyst,
        ticker=signal["ticker"],
        direction=signal["direction"],
    )
    if analyst == "smart_money":
        row = AttributionSignalsRow(
            **common,
            confidence=None,
            key_factors_json="[]",
            conviction=signal.get("conviction"),
            insiders_json=json.dumps(signal.get("insiders", [])),
            politicians_json=json.dumps(signal.get("politicians", [])),
            total_dollar_value=signal.get("total_dollar_value"),
        )
    else:
        row = AttributionSignalsRow(
            **common,
            confidence=signal.get("confidence"),
            key_factors_json=json.dumps(signal.get("key_factors", [])),
            top_headlines_json=(
                json.dumps(signal["top_headlines"])
                if analyst == "sentiment" and "top_headlines" in signal
                else None
            ),
            social_score_delta=(
                signal.get("social_score_delta") if analyst == "sentiment" else None
            ),
        )
    session.add(row)
    session.flush()


# ── AnalystEvidence ───────────────────────────────────────────────────

class AnalystEvidenceRow(Base):
    """One row per analyst per ticker per tick. Mirrors `AnalystEvidence` Pydantic shape."""

    __tablename__ = "analyst_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    analyst: Mapped[str] = mapped_column(String, index=True)
    ticker: Mapped[str] = mapped_column(String, index=True)

    lean: Mapped[str] = mapped_column(String)
    magnitude: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(String, default="")
    key_factors_json: Mapped[str] = mapped_column(String, default="[]")
    is_no_data: Mapped[bool] = mapped_column(Boolean, default=False)

    features_json: Mapped[str] = mapped_column(String, default="{}")
    feature_warnings_json: Mapped[str] = mapped_column(String, default="[]")


def save_analyst_evidence(
    session: Session,
    *,
    tick_id: str,
    analyst: str,
    ticker: str,
    verdict: dict,
    features: dict,
    feature_warnings: list[str],
) -> None:
    """Persist one AnalystEvidence row.

    Args:
        session: SQLAlchemy session used for the insert.
        tick_id: Identifier of the tick that produced this evidence.
        analyst: One of ``technical|fundamental|sentiment|smart_money``.
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        verdict: Dict matching the ``AnalystVerdict`` shape — must contain
            ``lean``, ``magnitude``, ``confidence``; optionally ``rationale``,
            ``key_factors``, ``is_no_data``.
        features: Raw feature dict fed to the analyst (e.g. RSI, ATR values).
        feature_warnings: Any warnings raised during feature extraction.

    Returns:
        None. The new row is flushed but **not** committed; the caller controls
        commit ordering so it can batch writes for the same tick.
    """
    row = AnalystEvidenceRow(
        tick_id=tick_id,
        recorded_at=datetime.now(tz=UTC),
        analyst=analyst,
        ticker=ticker,
        lean=verdict["lean"],
        magnitude=float(verdict["magnitude"]),
        confidence=float(verdict["confidence"]),
        rationale=verdict.get("rationale", ""),
        key_factors_json=json.dumps(verdict.get("key_factors", [])),
        is_no_data=bool(verdict.get("is_no_data", False)),
        features_json=json.dumps(features),
        feature_warnings_json=json.dumps(feature_warnings),
    )
    session.add(row)
    session.flush()


# ── TickerEvidence ────────────────────────────────────────────────────

class TickerEvidenceRow(Base):
    """One row per ticker per tick — aggregated cross-analyst stance."""

    __tablename__ = "ticker_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str] = mapped_column(String, index=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime)
    ticker: Mapped[str] = mapped_column(String, index=True)

    lean: Mapped[str] = mapped_column(String)
    magnitude: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    disagreement: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(String, default="")

    weights_json: Mapped[str] = mapped_column(String, default="{}")
    analyst_count: Mapped[int] = mapped_column(Integer, default=0)


def save_ticker_evidence(
    session: Session,
    *,
    tick_id: str,
    ticker: str,
    aggregate: dict,
    weights: dict,
    analyst_count: int,
) -> None:
    """Persist one TickerEvidence row.

    Args:
        session: SQLAlchemy session used for the insert.
        tick_id: Identifier of the tick that produced this evidence.
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        aggregate: Dict matching the ``TickerAggregate`` shape — must contain
            ``lean``, ``magnitude``, ``confidence``, ``disagreement``; optionally
            ``summary``.
        weights: Mapping of analyst name to numeric weight used during
            aggregation (e.g. ``{"technical": 1.0, ...}``).
        analyst_count: Total number of analysts whose evidence was aggregated.

    Returns:
        None. The new row is flushed but **not** committed; the caller controls
        commit ordering so it can batch writes for the same tick.
    """
    row = TickerEvidenceRow(
        tick_id=tick_id,
        recorded_at=datetime.now(tz=UTC),
        ticker=ticker,
        lean=aggregate["lean"],
        magnitude=float(aggregate["magnitude"]),
        confidence=float(aggregate["confidence"]),
        disagreement=float(aggregate["disagreement"]),
        summary=aggregate.get("summary", ""),
        weights_json=json.dumps(weights),
        analyst_count=int(analyst_count),
    )
    session.add(row)
    session.flush()


def make_engine(db_url: str = "sqlite://"):
    """Create a SQLAlchemy engine for the given URL. Default is an in-memory SQLite."""
    return create_engine(db_url)


def make_session_factory(engine):
    """Return a sessionmaker bound to `engine`."""
    return sessionmaker(bind=engine)


def create_all(engine) -> None:
    """Create all StockBot tables if they don't already exist (idempotent)."""
    Base.metadata.create_all(engine)


# ── ADK SessionService factory ────────────────────────────────────────


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
