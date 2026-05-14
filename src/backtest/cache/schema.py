"""SQLAlchemy DDL for the golden backtest cache store.

One SQLite file at ``backtests/cache/store.sqlite``.  Every time-bearing
column is indexed for fast point-in-time reads.

Critical correctness rule: filter on the *filing* / *publication* timestamp,
never on the *transaction* date — a Form 4 trade can predate its filing by
days, and using ``transaction_date`` would leak future information into the
analysts.

Column-name notes (deviations from the plan spec, reflecting live models):
- ``ohlcv_bars``: uses ``timestamp`` (datetime) to match ``OHLCBar.timestamp``;
  no ``adj_close`` or ``ticker`` column at the OHLCBar level (ticker is stored
  separately and associated via the store layer).
- ``market_meta``: ``ma_50`` / ``ma_200`` renamed to ``fifty_day_average`` /
  ``two_hundred_day_average`` to match ``StockStats`` field names.
- ``insider_trades``: no ``accession_no`` natural key on ``InsiderTrade`` — PK
  is a synthetic SHA-1 of the distinguishing fields.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Float, Index, Integer, String,
)
from sqlalchemy.orm import DeclarativeBase


# Bumped when the schema changes incompatibly; readers refuse mismatched versions.
SCHEMA_VERSION = 1


class CacheBase(DeclarativeBase):
    """Declarative base for every table in the cache store."""


class OHLCVBarRow(CacheBase):
    """Daily OHLCV bar.

    PK is ``(ticker, timestamp)`` where ``timestamp`` is the bar's datetime
    (matches ``OHLCBar.timestamp`` from the live model).  No ``adj_close``
    column — the live ``OHLCBar`` model does not carry one.
    """

    __tablename__ = "ohlcv_bars"

    ticker:    str      = Column(String,   primary_key=True)
    timestamp: datetime = Column(DateTime, primary_key=True)
    open:      float    = Column(Float)
    high:      float    = Column(Float)
    low:       float    = Column(Float)
    close:     float    = Column(Float)
    volume:    float    = Column(Float)

    __table_args__ = (Index("ix_ohlcv_ticker_ts", "ticker", "timestamp"),)


class MarketMetaRow(CacheBase):
    """Daily fundamentals / market-meta snapshot.

    PK is ``(ticker, as_of_date)``.  The moving-average column names match
    ``StockStats.fifty_day_average`` and ``StockStats.two_hundred_day_average``
    from the live model (the plan spec used ``ma_50`` / ``ma_200``).
    """

    __tablename__ = "market_meta"

    ticker:                str   = Column(String, primary_key=True)
    as_of_date:            date  = Column(Date,   primary_key=True)
    market_cap:            float = Column(Float)
    trailing_pe:           float = Column(Float)
    forward_pe:            float = Column(Float)
    beta:                  float = Column(Float)
    dividend_yield:        float = Column(Float)
    fifty_day_average:     float = Column(Float)
    two_hundred_day_average: float = Column(Float)
    last_price:            float = Column(Float)
    sector:                str   = Column(String)
    long_name:             str   = Column(String)

    __table_args__ = (Index("ix_meta_ticker_asof", "ticker", "as_of_date"),)


class FilingRow(CacheBase):
    """SEC filing.  PK is ``accession_no``."""

    __tablename__ = "filings"

    accession_no:         str      = Column(String,   primary_key=True)
    ticker:               str      = Column(String,   index=True)
    form_type:            str      = Column(String)
    filed_at:             datetime = Column(DateTime)
    title:                str      = Column(String)
    url:                  str      = Column(String)
    risk_factors_excerpt: str      = Column(String)
    mda_excerpt:          str      = Column(String)

    __table_args__ = (Index("ix_filings_ticker_filed", "ticker", "filed_at"),)


class NewsArticleRow(CacheBase):
    """News article.  PK is ``(ticker, url)``."""

    __tablename__ = "news_articles"

    ticker:       str      = Column(String,   primary_key=True)
    url:          str      = Column(String,   primary_key=True)
    headline:     str      = Column(String)
    summary:      str      = Column(String)
    source:       str      = Column(String)
    published_at: datetime = Column(DateTime)
    sentiment:    float    = Column(Float)

    __table_args__ = (Index("ix_news_ticker_pub", "ticker", "published_at"),)


class InsiderTradeRow(CacheBase):
    """Insider Form 4 common-stock row.

    The live ``InsiderTrade`` model has no ``accession_no`` field, so the PK is
    a synthetic SHA-1 of ``(ticker, insider_name, transaction_date, side,
    shares)`` — the minimal tuple that distinguishes one row from another.
    Additional narrative fields (``transaction_code``, ``is_10b5_1``,
    ``footnote``) are stored so the cache round-trips the full model.
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

    # Narrative fields added in Phase 5 of the live pipeline.
    transaction_code: str  = Column(String)
    is_10b5_1:        bool = Column(Boolean)
    footnote:         str  = Column(String)

    __table_args__ = (Index("ix_insider_ticker_filed", "ticker", "filed_at"),)


class PoliticianTradeRow(CacheBase):
    """Politician trade disclosure.  PK is a synthetic SHA-1 of the tuple."""

    __tablename__ = "politician_trades"

    row_hash:         str   = Column(String,  primary_key=True)
    ticker:           str   = Column(String,  index=True)
    politician:       str   = Column(String)
    chamber:          str   = Column(String)
    party:            str   = Column(String)
    side:             str   = Column(String)
    transaction_date: date  = Column(Date)
    disclosure_date:  date  = Column(Date)
    amount_min_usd:   float = Column(Float)
    amount_max_usd:   float = Column(Float)

    __table_args__ = (Index("ix_pol_ticker_disc", "ticker", "disclosure_date"),)


class NotableHolderRow(CacheBase):
    """13D / 13G beneficial-ownership filing.  PK is ``accession_no``."""

    __tablename__ = "notable_holders"

    accession_no: str      = Column(String,   primary_key=True)
    ticker:       str      = Column(String,   index=True)
    holder:       str      = Column(String)
    form_type:    str      = Column(String)
    intent:       str      = Column(String)
    is_amendment: bool     = Column(Boolean)
    filed_at:     datetime = Column(DateTime)
    url:          str      = Column(String)

    __table_args__ = (Index("ix_holders_ticker_filed", "ticker", "filed_at"),)


class CacheRunRow(CacheBase):
    """Audit log of fetcher runs.  Surrogate ``run_id`` PK."""

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
    """Single-row table holding schema version and creation timestamp."""

    __tablename__ = "meta"

    schema_version: int      = Column(Integer,  primary_key=True)
    created_at:     datetime = Column(DateTime)


def create_all(engine) -> None:
    """Create every table on the supplied SQLAlchemy engine.

    Parameters
    ----------
    engine:
        A SQLAlchemy engine pointing at the target SQLite file.
    """
    CacheBase.metadata.create_all(engine)
