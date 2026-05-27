"""SQL persistence layer. SQLAlchemy ORM for all durable state."""
from __future__ import annotations

import json
import os
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from data.timeguard import resolve_as_of


class Base(DeclarativeBase):
    pass


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
    opened_tag: Mapped[str] = mapped_column(String)
    closed_tag: Mapped[str] = mapped_column(String)
    opened_rationale: Mapped[str] = mapped_column(String)
    close_reason: Mapped[str] = mapped_column(String)

    # Nullable FK-style references linking a trade back to the originating tick.
    # opening_tick_id: copied from PositionThesis.opened_tick_id when executor.BUY
    #   writes the position. closing_tick_id: stamped by executor.SELL with the
    #   tick that triggered the close.
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
    # Enforce one stance row per ticker per tick so analytics joins never
    # encounter ambiguous duplicates (FU-06).
    __table_args__ = (UniqueConstraint("tick_id", "ticker", name="uq_ticker_stance_tick_ticker"),)

    id: Mapped[int]                 = mapped_column(Integer, primary_key=True, autoincrement=True)
    tick_id: Mapped[str]            = mapped_column(String, index=True)
    recorded_at: Mapped[datetime]   = mapped_column(DateTime)
    ticker: Mapped[str]             = mapped_column(String, index=True)
    preferred_weight: Mapped[float] = mapped_column(Float)
    conviction: Mapped[float]       = mapped_column(Float)
    rationale: Mapped[str]          = mapped_column(String)
    # horizon / target_price / stop_price dropped in iter-3 — the audit
    # found they were hallucinated 80 % of the time and never consumed
    # downstream (Bug #9, docs/backtest-audits/baseline-window-2025-09-iter-2.md).
    # close_reason / trim_reason also dropped in iter-3: the split-reason design
    # was replaced by the unified ``sell_reasons`` dict on ``StrategistDecision``.
    lifecycle_action: Mapped[str]   = mapped_column(String, index=True)
    decision_tag: Mapped[str]       = mapped_column(String, index=True)


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
            ``TickerStance.model_dump(mode="json")``). Must contain ``ticker``.
            ``preferred_weight`` and ``conviction`` are legacy DB columns
            (user-gated rename per spec-b-plan-3) — they default to 0.0 when
            absent from the dump (Band 3 deleted them from ``TickerStance``).
            ``rationale`` is required on ``open`` stances; optional on others.
            Remaining lifecycle fields may be missing or ``None``.
        lifecycle_action: One of ``"buy" | "sell" | "update"`` (iter-3
            three-verb canonical form) — the stance intent verb, now sourced
            from ``stance.intent`` directly.  Saved alongside the stance so
            that downstream analytics can filter without recomputing.

    Returns:
        None. The new row is added and flushed but **not** committed; the caller
        controls commit ordering so that the stance write can be batched with
        other writes for the same tick.
    """
    # ``preferred_weight`` and ``conviction`` are legacy DB columns that are
    # user-gated for rename (see spec-b-plan-3).  Band 3 deleted them from
    # ``TickerStance``, so stance dicts produced by ``model_dump`` no longer
    # carry them.  Fall back to 0.0 so the non-nullable columns receive a value
    # until the column rename migration runs.
    # ``horizon`` / ``target_price`` / ``stop_price`` were dropped from
    # ``TickerStanceRow`` in iter-3; they are no longer read from the
    # stance dict.  Any caller still passing those keys is silently
    # ignored at the dict-access level — the ORM column no longer exists.
    row = TickerStanceRow(
        tick_id=tick_id,
        recorded_at=recorded_at,
        ticker=stance["ticker"],
        preferred_weight=stance.get("preferred_weight", 0.0),
        conviction=stance.get("conviction", 0.0),
        rationale=stance.get("rationale") or "",  # only populated on buy stances
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
        # ``recorded_at`` is set by SnapshotterAgent from state["as_of"] (backtest
        # path) or wall-clock (live path), so the snap dict always carries the
        # correct value.  The fallback here only fires if the caller omits it
        # entirely (e.g. legacy tests that pre-date the as_of migration).
        recorded_at=resolve_as_of(
            snap.get("recorded_at"),
            allow_wallclock=True,
            site="persistence.save_portfolio_snapshot",
        ),
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


# ── AnalystEvidence ───────────────────────────────────────────────────

class AnalystEvidenceRow(Base):
    """One row per analyst per ticker per tick. Mirrors `AnalystEvidence` Pydantic shape."""

    __tablename__ = "analyst_evidence"

    # Composite lookup index for KB-readiness — Phase 5 (Task 12).
    # Speeds up queries that filter by (analyst, ticker) and order by recorded_at,
    # which is the dominant access pattern for evidence retrieval (e.g. "give me the
    # last N technical verdicts for AAPL"). No data migration required; SQLite/Postgres
    # build the index lazily when the schema is created.
    __table_args__ = (
        Index("ix_analyst_evidence_lookup", "analyst", "ticker", "recorded_at"),
    )

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
    recorded_at: datetime | None = None,
) -> None:
    """Persist one AnalystEvidence row.

    Args:
        session: SQLAlchemy session used for the insert.
        tick_id: Identifier of the tick that produced this evidence.
        analyst: One of ``technical|fundamental|news|social|smart_money``.
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        verdict: The dict produced by ``AnalystVerdict.model_dump()`` from
            ``src/contract/evidence.py``; all fields including ``rationale``
            are expected to be present.  The ``.get`` fallbacks below only
            protect against an out-of-contract partial dict — they are not
            licence to construct one.
        features: Raw feature dict fed to the analyst (e.g. RSI, ATR values).
        feature_warnings: Any warnings raised during feature extraction.
        recorded_at: Timestamp to stamp the row with.  Pass ``state["as_of"]``
            in backtest mode for deterministic replay.  Defaults to wall-clock
            when ``None`` (preserves live behaviour).

    Returns:
        None. The new row is flushed but **not** committed; the caller controls
        commit ordering so it can batch writes for the same tick.
    """
    row = AnalystEvidenceRow(
        tick_id=tick_id,
        recorded_at=resolve_as_of(
            recorded_at,
            allow_wallclock=True,
            site="persistence.save_analyst_evidence",
        ),
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
    recorded_at: datetime | None = None,
) -> None:
    """Persist one TickerEvidence row.

    Args:
        session: SQLAlchemy session used for the insert.
        tick_id: Identifier of the tick that produced this evidence.
        ticker: Stock ticker symbol (e.g. ``"AAPL"``).
        aggregate: The dict produced by ``TickerEvidence.model_dump()`` from
            ``src/contract/evidence.py``; all fields including ``summary``
            are expected to be present.  The ``.get`` fallback below only
            protects against an out-of-contract partial dict — it is not
            licence to construct one.
        weights: Mapping of analyst name to numeric weight used during
            aggregation (e.g. ``{"technical": 1.0, ...}``).
        analyst_count: Total number of analysts whose evidence was aggregated.
        recorded_at: Timestamp to stamp the row with.  Pass ``state["as_of"]``
            in backtest mode for deterministic replay.  Defaults to wall-clock
            when ``None`` (preserves live behaviour).

    Returns:
        None. The new row is flushed but **not** committed; the caller controls
        commit ordering so it can batch writes for the same tick.
    """
    row = TickerEvidenceRow(
        tick_id=tick_id,
        recorded_at=resolve_as_of(
            recorded_at,
            allow_wallclock=True,
            site="persistence.save_ticker_evidence",
        ),
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


def make_session_service(
    db_url: str | None = None,
):
    """Construct a session service for the current process.

    Parameters
    ----------
    db_url
        Optional SQLAlchemy-style DB URL.  When ``None``, falls back
        to the ``DATABASE_URL`` environment variable (live path).
        When supplied, used directly (backtest passes a
        ``sqlite+aiosqlite:///runs/<run-id>/session.sqlite`` URL).

    Returns
    -------
    DatabaseSessionService
        A configured ``DatabaseSessionService``.  In-memory mode is
        no longer supported by this factory — tests that want an
        in-memory database pass ``sqlite+aiosqlite:///:memory:``.

    Raises
    ------
    RuntimeError
        When neither ``db_url`` nor the ``DATABASE_URL`` environment
        variable is set — both are absent and there is no safe default
        to fall back on.
    """
    from google.adk.sessions import DatabaseSessionService

    resolved = db_url or os.environ.get("DATABASE_URL")

    if not resolved:
        raise RuntimeError(
            "make_session_service: no db_url and no DATABASE_URL env var set. "
            "Pass db_url= explicitly or set DATABASE_URL in the environment."
        )

    return DatabaseSessionService(db_url=resolved)
