"""SQLAlchemy DDL for the golden backtest cache store.

One SQLite file at ``backtests/cache/store.sqlite``.  Every time-bearing
column is indexed for fast point-in-time reads.

Critical correctness rule — PIT filters MUST use the *filing* or *publication*
timestamp, never the *transaction* date.  A Form 4 insider trade can be
transacted days before its filing date; filtering on ``transaction_date`` would
leak future information and silently invalidate every backtest.  Same principle
applies to SEC filings (``filed_at``) and news (``published_at``).

For politician trades the canonical PIT column is
``COALESCE(disclosure_date, transaction_date)`` — the STOCK Act allows up to
45 days between transaction and public disclosure.

Adaptations vs the original plan:
- ``OHLCVBarRow``: uses ``ts DATETIME`` (from OHLCBar.timestamp) as PK with
  ``ticker``; no ``adj_close`` column (field absent from OHLCBar model).
- ``CompanyRatiosRow`` replaces the plan's ``MarketMetaRow`` / ``market_meta``
  table; columns mirror ``CompanyRatios`` fields exactly (``fifty_day_average``,
  ``two_hundred_day_average``, ``last_price`` instead of plan's ``ma_50``,
  ``ma_200``; adds ``long_name``, ``sector`` from model).
- ``InsiderTradeRow``: adds ``transaction_code``, ``is_10b5_1``, ``footnote``
  to support the Phase 5 extras on ``InsiderTrade`` (model has ``extra="forbid"``
  so schema must be complete).
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase

# Bump this when the DDL changes in a backwards-incompatible way.
# 2026-Q2 bump: politician_trades Date → DateTime (intraday PIT enforcement).
SCHEMA_VERSION = 2


class CacheBase(DeclarativeBase):
    """Declarative base for every table in the cache store."""


class OHLCVBarRow(CacheBase):
    """Daily OHLCV bar.  PK ``(ticker, ts)``.

    ``ts`` stores the ``OHLCBar.timestamp`` datetime (midnight UTC per bar).
    The date portion is used for range queries via SQLite's ``date(ts)``.
    No ``adj_close`` column — ``OHLCBar`` does not carry that field.
    """

    __tablename__ = "ohlcv_bars"

    ticker: str      = Column(String,   primary_key=True)
    ts:     datetime = Column(DateTime, primary_key=True)
    open:   float    = Column(Float)
    high:   float    = Column(Float)
    low:    float    = Column(Float)
    close:  float    = Column(Float)
    volume: float    = Column(Float)

    __table_args__ = (Index("ix_ohlcv_ticker_ts", "ticker", "ts"),)


class CompanyRatiosRow(CacheBase):
    """Point-in-time fundamentals snapshot.  PK ``(ticker, as_of_date)``.

    Replaces the plan's ``MarketMetaRow`` / ``market_meta`` table.  Column
    names mirror ``CompanyRatios`` exactly so ``model_validate(row,
    from_attributes=True)`` round-trips cleanly.
    """

    __tablename__ = "company_ratios"

    ticker:                 str   = Column(String, primary_key=True)
    as_of_date:             date  = Column(Date,   primary_key=True)

    # Optional fundamentals — all nullable.
    long_name:              str   = Column(String)
    sector:                 str   = Column(String)
    market_cap:             float = Column(Float)
    trailing_pe:            float = Column(Float)
    forward_pe:             float = Column(Float)
    beta:                   float = Column(Float)
    dividend_yield:         float = Column(Float)
    fifty_day_average:      float = Column(Float)
    two_hundred_day_average: float = Column(Float)
    last_price:             float = Column(Float)

    # XBRL-derived ratios — populated by the pit_composite provider from
    # SEC ``EntityFacts.query().by_concept().as_of()`` calls.  All nullable
    # because XBRL is sparse (ADRs, recent IPOs, foreign filers can lack
    # any of these concepts).  ``peg`` is intentionally always None — no
    # PIT-correct source exists for the forward-growth term; the column is
    # carried for forward compatibility / schema parity with the model.
    profit_margin:          float = Column(Float)
    debt_to_equity:         float = Column(Float)
    roe:                    float = Column(Float)
    revenue_growth_yoy:     float = Column(Float)
    free_cash_flow:         float = Column(Float)
    peg:                    float = Column(Float)

    __table_args__ = (Index("ix_ratios_ticker_asof", "ticker", "as_of_date"),)


class FilingRow(CacheBase):
    """SEC filing.  PK ``accession_no``.

    ``ticker`` is stored from the write-time parameter (``Filing.ticker``
    carries it, but accession_no is the natural dedup key).
    ``filed_at`` is the PIT filter column — never use the report period.
    """

    __tablename__ = "filings"

    accession_no:         str      = Column(String,   primary_key=True)
    ticker:               str      = Column(String,   index=True)
    form_type:            str      = Column(String)
    filed_at:             datetime = Column(DateTime)
    title:                str      = Column(String)
    url:                  str      = Column(String)
    risk_factors_excerpt: str      = Column(Text)
    mda_excerpt:          str      = Column(Text)

    __table_args__ = (Index("ix_filings_ticker_filed", "ticker", "filed_at"),)


class NewsArticleRow(CacheBase):
    """News article.  PK ``(ticker, url)``.

    ``published_at`` is the PIT filter column.  ``sentiment`` is nullable —
    ``NewsArticle.sentiment`` is ``float | None``.
    """

    __tablename__ = "news_articles"

    ticker:       str      = Column(String,   primary_key=True)
    url:          str      = Column(String,   primary_key=True)
    headline:     str      = Column(String)
    summary:      str      = Column(String)
    source:       str      = Column(String)
    published_at: datetime = Column(DateTime)
    sentiment:    float    = Column(Float)   # nullable

    __table_args__ = (Index("ix_news_ticker_pub", "ticker", "published_at"),)


class InsiderTradeRow(CacheBase):
    """Insider Form 4 row.  PK ``row_hash`` (synthetic SHA-1).

    ``InsiderTrade`` does not carry an ``accession_no`` field (that lives on
    ``Form4Bundle``).  A synthetic hash of the identifying fields
    ``(ticker, insider_name, transaction_date, side, shares, filed_at)`` is
    used as the surrogate PK so write operations are idempotent.

    PIT filter MUST use ``filed_at``, not ``transaction_date`` — the two can
    differ by days, and filtering on ``transaction_date`` leaks future
    information.

    Includes the Phase-5 narrative fields (``transaction_code``, ``is_10b5_1``,
    ``footnote``) so ``model_validate(row, from_attributes=True)`` round-trips
    through ``InsiderTrade`` (which has ``extra="forbid"``).
    """

    __tablename__ = "insider_trades"

    row_hash:         str      = Column(String,   primary_key=True)
    ticker:           str      = Column(String,   index=True)
    insider_name:     str      = Column(String)
    insider_title:    str      = Column(String)
    side:             str      = Column(String)
    shares:           float    = Column(Float)
    price_per_share:  float    = Column(Float)
    transaction_date: date     = Column(Date)
    filed_at:         datetime = Column(DateTime)
    form_type:        str      = Column(String)

    # Phase-5 narrative supplement.
    transaction_code: str  = Column(String)   # nullable — e.g. "P", "S", "A"
    is_10b5_1:        bool = Column(Boolean)  # False when NULL on read
    footnote:         str  = Column(Text)     # nullable

    __table_args__ = (Index("ix_insider_ticker_filed", "ticker", "filed_at"),)


class PoliticianTradeRow(CacheBase):
    """Politician trade disclosure.  PK is a synthetic SHA-1 of the tuple.

    PIT filter uses ``COALESCE(disclosure_date, transaction_date)`` — the
    STOCK Act allows up to 45 days between transaction and public disclosure.
    All non-key fields are nullable to match the ``PoliticianTrade`` model.
    """

    __tablename__ = "politician_trades"

    row_hash:         str      = Column(String, primary_key=True)
    ticker:           str      = Column(String, index=True)
    politician:       str      = Column(String)
    chamber:          str      = Column(String)    # nullable
    party:            str      = Column(String)    # nullable
    side:             str      = Column(String)
    # NOTE: 2026-Q2 — migrated from Date to DateTime so the cache can
    # represent the intraday "next business day" disclosure visibility
    # rule.  Date-only upstream rows are stored as 00:00:00 UTC of the
    # next business day to prevent same-day leakage (STOCK Act allows
    # disclosure any time on the disclosure_date).
    transaction_date: datetime = Column(DateTime)
    disclosure_date:  datetime = Column(DateTime)  # nullable
    amount_min_usd:   float    = Column(Float)     # nullable
    amount_max_usd:   float    = Column(Float)     # nullable

    __table_args__ = (
        Index("ix_pol_ticker_disc", "ticker", "disclosure_date"),
    )


class NotableHolderRow(CacheBase):
    """SC 13D / 13G / 13F beneficial-ownership filing.  PK ``accession_no``.

    ``filed_at`` is the PIT filter column.
    ``url`` is nullable — ``NotableHolder.url`` is ``str | None``.
    """

    __tablename__ = "notable_holders"

    accession_no: str      = Column(String,   primary_key=True)
    ticker:       str      = Column(String,   index=True)
    holder:       str      = Column(String)
    form_type:    str      = Column(String)
    intent:       str      = Column(String)
    is_amendment: bool     = Column(Boolean)
    filed_at:     datetime = Column(DateTime)
    url:          str      = Column(String)   # nullable

    __table_args__ = (Index("ix_holders_ticker_filed", "ticker", "filed_at"),)


class CacheRunRow(CacheBase):
    """Current-status ledger for fetcher runs.  Surrogate ``run_id`` PK.

    Holds **one row per** ``(window_key, ticker, domain)`` triple — not an
    append-only log.  Each fetch attempt supersedes (replaces) any prior row
    for the same triple so the table always reflects the latest known status.
    The surrogate ``run_id`` PK is retained for uniqueness; the ledger
    invariant is enforced by the fetcher (delete-then-insert in one
    transaction), not by a database constraint.

    This design means the audit script can unconditionally flag any
    ``status='error'`` row without false-positives from prior failed attempts.
    """

    __tablename__ = "cache_runs"

    run_id:          str      = Column(String,   primary_key=True)
    started_at:      datetime = Column(DateTime)
    finished_at:     datetime = Column(DateTime)
    window_key:      str      = Column(String)
    ticker:          str      = Column(String,   index=True)
    domain:          str      = Column(String,   index=True)
    source_provider: str      = Column(String)
    rows_written:    int      = Column(Integer)
    status:          str      = Column(String)   # "ok" | "error" | "partial"
    error:           str      = Column(String)


class MetaRow(CacheBase):
    """Single-row table holding schema version + creation timestamp."""

    __tablename__ = "meta"

    schema_version: int      = Column(Integer,  primary_key=True)
    created_at:     datetime = Column(DateTime)


def create_all(engine) -> None:
    """Create every table on the supplied SQLAlchemy engine (idempotent)."""
    CacheBase.metadata.create_all(engine)
