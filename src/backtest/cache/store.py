"""Cache store façade — read/write keyed on (ticker, as_of, domain).

Readers honour the point-in-time filter: rows whose canonical timestamp is
after the supplied ``as_of`` are never returned.  Writers are idempotent on
the primary key — re-running the fetcher is safe.

Adaptation notes vs. plan spec:
- ``OHLCBar`` (live model) uses ``timestamp: datetime``, not ``date: date``,
  and carries no ``ticker`` or ``adj_close`` field.  ``write_ohlcv`` /
  ``read_ohlcv`` accept ``ticker`` as a separate arg; start/end bounds are
  ``datetime`` objects.
- ``StockStats`` uses ``fifty_day_average`` / ``two_hundred_day_average``
  rather than ``ma_50`` / ``ma_200``; the ``MarketMetaRow`` columns mirror
  those names.
- ``InsiderTrade`` has no natural ``accession_no`` — a SHA-1 of the
  distinguishing fields is used as the synthetic PK (same logic as
  ``PoliticianTrade``).
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

def _naive(dt: datetime) -> datetime:
    """Strip timezone info from ``dt``, converting to UTC first if needed.

    SQLite stores datetimes as naive ISO strings; comparisons against
    timezone-aware Python objects produce incorrect results.  We normalise all
    bounds to naive UTC before issuing queries, and re-attach UTC when
    constructing models on the way out.

    Parameters
    ----------
    dt:
        A timezone-aware or naive datetime.

    Returns
    -------
    datetime
        The same instant expressed as a naive UTC datetime.
    """
    if dt.tzinfo is not None:
        return dt.astimezone(UTC).replace(tzinfo=None)
    return dt


def _utc(dt: datetime) -> datetime:
    """Attach ``timezone.utc`` to a naive datetime read back from SQLite.

    Parameters
    ----------
    dt:
        A naive datetime as returned by the SQLAlchemy SQLite driver.

    Returns
    -------
    datetime
        The same instant with UTC timezone attached.
    """
    if dt is None:
        return dt
    if dt.tzinfo is not None:
        return dt
    return dt.replace(tzinfo=UTC)


from backtest.cache.schema import (
    SCHEMA_VERSION,
    CacheRunRow,
    FilingRow,
    InsiderTradeRow,
    MarketMetaRow,
    MetaRow,
    NewsArticleRow,
    NotableHolderRow,
    OHLCVBarRow,
    PoliticianTradeRow,
    create_all,
)
from data.models import (
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
    StockStats,
)


class CachedDataStore:
    """SQLite-backed read/write façade over the golden cache.

    Methods are grouped by domain; every reader applies the point-in-time
    filter required for lookahead-free backtests.  Writers are idempotent —
    duplicate inserts are silently ignored via ``ON CONFLICT DO NOTHING``.

    Parameters
    ----------
    path:
        Filesystem path to the SQLite file.  Created (along with parent
        directories) if it does not already exist.
    """

    def __init__(self, path: Path) -> None:
        """Open (or create) the SQLite file at ``path``; initialise schema."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._engine = create_engine(f"sqlite:///{path}", future=True)
        create_all(self._engine)
        self._ensure_meta()

    # ── Schema version ────────────────────────────────────────────────────

    def _ensure_meta(self) -> None:
        """Insert the schema-version row if the meta table is still empty."""
        with Session(self._engine) as s:
            existing = s.execute(select(MetaRow)).scalar_one_or_none()
            if existing is None:
                s.add(MetaRow(
                    schema_version=SCHEMA_VERSION,
                    created_at=datetime.now(tz=UTC),
                ))
                s.commit()

    # ── OHLCV ─────────────────────────────────────────────────────────────

    def write_ohlcv(self, ticker: str, bars: list[OHLCBar]) -> None:
        """Upsert daily OHLCV bars for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol these bars belong to.
        bars:
            List of ``OHLCBar`` objects from the live market provider.
        """
        with Session(self._engine) as s:
            for b in bars:
                stmt = sqlite_insert(OHLCVBarRow).values(
                    ticker=ticker,
                    timestamp=_naive(b.timestamp),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                ).on_conflict_do_nothing(index_elements=["ticker", "timestamp"])
                s.execute(stmt)
            s.commit()

    def read_ohlcv(
        self, ticker: str, start: date, end: date,
    ) -> list[OHLCBar]:
        """Return bars whose calendar date falls in ``[start, end]`` inclusive.

        The comparison is done at the date level to avoid ambiguity when bars
        carry a specific intraday time (e.g. the close bar at 16:00 ET).

        Parameters
        ----------
        ticker:
            The equity symbol to query.
        start:
            First calendar date to include (inclusive).
        end:
            Last calendar date to include (inclusive).

        Returns
        -------
        list[OHLCBar]
            Bars sorted by timestamp ascending.
        """
        # Expand date bounds to cover the full day.  End becomes midnight at
        # the start of the *next* day so that any intraday timestamp on ``end``
        # day (e.g. 16:00 close) is captured by the strict-less-than bound.
        start_dt = datetime(start.year, start.month, start.day)
        end_dt   = datetime(end.year,   end.month,   end.day) + timedelta(days=1)

        with Session(self._engine) as s:
            rows = s.execute(
                select(OHLCVBarRow)
                .where(
                    OHLCVBarRow.ticker    == ticker,
                    OHLCVBarRow.timestamp >= start_dt,
                    OHLCVBarRow.timestamp <  end_dt,   # strict-less-than covers full end day
                )
                .order_by(OHLCVBarRow.timestamp)
            ).scalars().all()

            # Re-attach UTC timezone stripped by the SQLite driver on read.
            # ticker is not a field on OHLCBar — it is stored only in the DB row.
            return [
                OHLCBar(
                    timestamp=_utc(row.timestamp),
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                )
                for row in rows
            ]

    # ── Market meta ───────────────────────────────────────────────────────

    def write_market_meta(
        self, ticker: str, snapshot: StockStats, as_of_date,
    ) -> None:
        """Upsert one market-meta snapshot for ``(ticker, as_of_date)``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        snapshot:
            A ``StockStats`` object from the live market provider.
        as_of_date:
            The date this snapshot was captured (used as PK).
        """
        with Session(self._engine) as s:
            stmt = sqlite_insert(MarketMetaRow).values(
                ticker=ticker,
                as_of_date=as_of_date,
                market_cap=snapshot.market_cap,
                trailing_pe=snapshot.trailing_pe,
                forward_pe=snapshot.forward_pe,
                beta=snapshot.beta,
                dividend_yield=snapshot.dividend_yield,
                fifty_day_average=snapshot.fifty_day_average,
                two_hundred_day_average=snapshot.two_hundred_day_average,
                last_price=snapshot.last_price,
                sector=snapshot.sector,
                long_name=snapshot.long_name,
            ).on_conflict_do_nothing(
                index_elements=["ticker", "as_of_date"],
            )
            s.execute(stmt)
            s.commit()

    def read_market_meta(
        self, ticker: str, as_of: datetime,
    ) -> StockStats | None:
        """Return the latest meta snapshot with ``as_of_date <= as_of.date()``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling; no snapshot newer than this is returned.

        Returns
        -------
        StockStats | None
            The most recent snapshot, or ``None`` if no cached data exists.
        """
        with Session(self._engine) as s:
            row = s.execute(
                select(MarketMetaRow)
                .where(
                    MarketMetaRow.ticker      == ticker,
                    MarketMetaRow.as_of_date  <= as_of.date(),
                )
                .order_by(MarketMetaRow.as_of_date.desc())
                .limit(1)
            ).scalar_one_or_none()

            if row is None:
                return None

            # Reconstruct a StockStats with an empty history list; the caller
            # (cache provider) is responsible for separately attaching OHLCV bars.
            return StockStats(
                ticker=ticker,
                history=[],
                market_cap=row.market_cap,
                trailing_pe=row.trailing_pe,
                forward_pe=row.forward_pe,
                beta=row.beta,
                dividend_yield=row.dividend_yield,
                fifty_day_average=row.fifty_day_average,
                two_hundred_day_average=row.two_hundred_day_average,
                last_price=row.last_price,
                sector=row.sector,
                long_name=row.long_name,
            )

    # ── News ──────────────────────────────────────────────────────────────

    def write_news(self, ticker: str, articles: list[NewsArticle]) -> None:
        """Upsert news articles for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol these articles are about.
        articles:
            List of ``NewsArticle`` objects from the live news provider.
        """
        with Session(self._engine) as s:
            for a in articles:
                stmt = sqlite_insert(NewsArticleRow).values(
                    ticker=ticker,
                    url=a.url,
                    headline=a.headline,
                    summary=a.summary,
                    source=a.source,
                    # Store as naive UTC so SQLite comparisons work correctly.
                    published_at=_naive(a.published_at),
                    sentiment=a.sentiment,
                ).on_conflict_do_nothing(index_elements=["ticker", "url"])
                s.execute(stmt)
            s.commit()

    def read_news(
        self,
        ticker: str,
        as_of: datetime,
        lookback_days: int = 30,
    ) -> list[NewsArticle]:
        """Return articles in ``(as_of - lookback_days, as_of]``, descending.

        The upper bound is the point-in-time filter — articles published after
        ``as_of`` are never returned regardless of lookback.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling.
        lookback_days:
            How many days back from ``as_of`` to include.

        Returns
        -------
        list[NewsArticle]
            Sorted by ``published_at`` descending (newest first).
        """
        # Normalise to naive UTC before comparing against SQLite-stored naive datetimes.
        as_of_naive = _naive(as_of)
        lower_naive = _naive(as_of - timedelta(days=lookback_days))

        with Session(self._engine) as s:
            rows = s.execute(
                select(NewsArticleRow)
                .where(
                    NewsArticleRow.ticker       == ticker,
                    NewsArticleRow.published_at <= as_of_naive,
                    NewsArticleRow.published_at >  lower_naive,
                )
                .order_by(NewsArticleRow.published_at.desc())
            ).scalars().all()

            # Re-attach UTC timezone on the way out so callers get aware datetimes.
            return [
                NewsArticle(
                    ticker=row.ticker,
                    url=row.url,
                    headline=row.headline,
                    summary=row.summary or "",
                    source=row.source or "",
                    published_at=_utc(row.published_at),
                    sentiment=row.sentiment,
                )
                for row in rows
            ]

    # ── Filings ───────────────────────────────────────────────────────────

    def write_filings(self, ticker: str, filings: list[Filing]) -> None:
        """Upsert SEC filings for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        filings:
            List of ``Filing`` objects from the live EDGAR provider.
        """
        with Session(self._engine) as s:
            for f in filings:
                stmt = sqlite_insert(FilingRow).values(
                    accession_no=f.accession_no,
                    ticker=ticker,
                    form_type=f.form_type,
                    filed_at=f.filed_at,
                    title=f.title,
                    url=f.url,
                    risk_factors_excerpt=f.risk_factors_excerpt,
                    mda_excerpt=f.mda_excerpt,
                ).on_conflict_do_nothing(index_elements=["accession_no"])
                s.execute(stmt)
            s.commit()

    def read_filings(
        self,
        ticker: str,
        as_of: datetime,
        lookback_days: int = 365,
    ) -> list[Filing]:
        """Return filings with ``filed_at <= as_of`` within the lookback window.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling.
        lookback_days:
            How many days back from ``as_of`` to include.

        Returns
        -------
        list[Filing]
            Sorted by ``filed_at`` descending.
        """
        lower = as_of - timedelta(days=lookback_days)

        with Session(self._engine) as s:
            rows = s.execute(
                select(FilingRow)
                .where(
                    FilingRow.ticker   == ticker,
                    FilingRow.filed_at <= as_of,
                    FilingRow.filed_at >  lower,
                )
                .order_by(FilingRow.filed_at.desc())
            ).scalars().all()

            return [
                Filing.model_validate(r, from_attributes=True)
                for r in rows
            ]

    # ── Insider trades ────────────────────────────────────────────────────

    @staticmethod
    def _insider_hash(
        ticker: str, trade: InsiderTrade,
    ) -> str:
        """Compute a stable SHA-1 key for one insider trade row.

        ``InsiderTrade`` has no natural accession number, so we derive a
        deterministic identifier from the fields that uniquely distinguish
        one reported transaction.

        Parameters
        ----------
        ticker:
            The equity symbol.
        trade:
            The insider trade to fingerprint.

        Returns
        -------
        str
            Hex SHA-1 digest.
        """
        key = "|".join([
            ticker,
            trade.insider_name,
            str(trade.transaction_date),
            trade.side,
            str(trade.shares),
        ])
        return hashlib.sha1(key.encode()).hexdigest()

    def write_insider_trades(
        self, ticker: str, trades: list[InsiderTrade],
    ) -> None:
        """Upsert Form 4 common-stock transactions for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        trades:
            List of ``InsiderTrade`` objects from the live EDGAR provider.
        """
        with Session(self._engine) as s:
            for t in trades:
                row_hash = self._insider_hash(ticker, t)
                stmt = sqlite_insert(InsiderTradeRow).values(
                    row_hash=row_hash,
                    ticker=ticker,
                    insider_name=t.insider_name,
                    insider_title=t.insider_title,
                    side=t.side,
                    shares=t.shares,
                    price_per_share=t.price_per_share,
                    transaction_date=t.transaction_date,
                    filed_at=t.filed_at,
                    form_type=t.form_type,
                    transaction_code=t.transaction_code,
                    is_10b5_1=t.is_10b5_1,
                    footnote=t.footnote,
                ).on_conflict_do_nothing(index_elements=["row_hash"])
                s.execute(stmt)
            s.commit()

    def read_insider_trades(
        self,
        ticker: str,
        as_of: datetime,
        lookback_days: int = 90,
    ) -> list[InsiderTrade]:
        """Return insider trades filtered by ``filed_at`` (never ``transaction_date``).

        Form 4 trades can be transacted days before they are filed; filtering
        on ``transaction_date`` would leak future information into the analysts.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling.
        lookback_days:
            How many days back from ``as_of`` to include.

        Returns
        -------
        list[InsiderTrade]
            Sorted by ``filed_at`` descending.
        """
        lower = as_of - timedelta(days=lookback_days)

        with Session(self._engine) as s:
            rows = s.execute(
                select(InsiderTradeRow)
                .where(
                    InsiderTradeRow.ticker   == ticker,
                    InsiderTradeRow.filed_at <= as_of,
                    InsiderTradeRow.filed_at >  lower,
                )
                .order_by(InsiderTradeRow.filed_at.desc())
            ).scalars().all()

            return [
                InsiderTrade(
                    ticker=row.ticker,
                    insider_name=row.insider_name,
                    insider_title=row.insider_title,
                    side=row.side,
                    shares=row.shares,
                    price_per_share=row.price_per_share,
                    transaction_date=row.transaction_date,
                    filed_at=row.filed_at,
                    form_type=row.form_type,
                    transaction_code=row.transaction_code,
                    is_10b5_1=bool(row.is_10b5_1),
                    footnote=row.footnote,
                )
                for row in rows
            ]

    # ── Politician trades ─────────────────────────────────────────────────

    @staticmethod
    def _politician_hash(ticker: str, trade: PoliticianTrade) -> str:
        """Compute a stable SHA-1 key for one politician trade row.

        Parameters
        ----------
        ticker:
            The equity symbol.
        trade:
            The politician trade to fingerprint.

        Returns
        -------
        str
            Hex SHA-1 digest.
        """
        key = "|".join([
            ticker,
            trade.politician,
            str(trade.transaction_date),
            trade.side,
            str(trade.amount_min_usd),
            str(trade.amount_max_usd),
        ])
        return hashlib.sha1(key.encode()).hexdigest()

    def write_politician_trades(
        self, ticker: str, trades: list[PoliticianTrade],
    ) -> None:
        """Upsert politician trade disclosures for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        trades:
            List of ``PoliticianTrade`` objects from the live FMP provider.
        """
        with Session(self._engine) as s:
            for t in trades:
                row_hash = self._politician_hash(ticker, t)
                stmt = sqlite_insert(PoliticianTradeRow).values(
                    row_hash=row_hash,
                    ticker=ticker,
                    politician=t.politician,
                    chamber=t.chamber,
                    party=t.party,
                    side=t.side,
                    transaction_date=t.transaction_date,
                    disclosure_date=t.disclosure_date,
                    amount_min_usd=t.amount_min_usd,
                    amount_max_usd=t.amount_max_usd,
                ).on_conflict_do_nothing(index_elements=["row_hash"])
                s.execute(stmt)
            s.commit()

    def read_politician_trades(
        self,
        ticker: str,
        as_of: datetime,
        lookback_days: int = 90,
    ) -> list[PoliticianTrade]:
        """Return politician trades using ``COALESCE(disclosure_date, transaction_date)`` as the PIT filter.

        Disclosure is the correct filter: the public only learns of the trade
        when it is disclosed (up to 45 days after the transaction under the
        STOCK Act).  Using ``transaction_date`` would leak future information.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling.
        lookback_days:
            How many days back from ``as_of`` to include.

        Returns
        -------
        list[PoliticianTrade]
            Sorted by disclosure date descending.
        """
        lower = (as_of - timedelta(days=lookback_days)).date()
        pit = func.coalesce(
            PoliticianTradeRow.disclosure_date,
            PoliticianTradeRow.transaction_date,
        )

        with Session(self._engine) as s:
            rows = s.execute(
                select(PoliticianTradeRow)
                .where(
                    PoliticianTradeRow.ticker == ticker,
                    pit <= as_of.date(),
                    pit >  lower,
                )
                .order_by(pit.desc())
            ).scalars().all()

            return [
                PoliticianTrade.model_validate(r, from_attributes=True)
                for r in rows
            ]

    # ── Notable holders ───────────────────────────────────────────────────

    def write_notable_holders(
        self, ticker: str, holders: list[NotableHolder],
    ) -> None:
        """Upsert 13D/13G beneficial-ownership filings for ``ticker``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        holders:
            List of ``NotableHolder`` objects from the live EDGAR provider.
        """
        with Session(self._engine) as s:
            for h in holders:
                stmt = sqlite_insert(NotableHolderRow).values(
                    accession_no=h.accession_no,
                    ticker=ticker,
                    holder=h.holder,
                    form_type=h.form_type,
                    intent=h.intent,
                    is_amendment=h.is_amendment,
                    filed_at=h.filed_at,
                    url=h.url,
                ).on_conflict_do_nothing(index_elements=["accession_no"])
                s.execute(stmt)
            s.commit()

    def read_notable_holders(
        self,
        ticker: str,
        as_of: datetime,
        lookback_days: int = 365,
    ) -> list[NotableHolder]:
        """Return 13D/13G filings with ``filed_at <= as_of``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        as_of:
            Point-in-time ceiling.
        lookback_days:
            How many days back from ``as_of`` to include.

        Returns
        -------
        list[NotableHolder]
            Sorted by ``filed_at`` descending.
        """
        lower = as_of - timedelta(days=lookback_days)

        with Session(self._engine) as s:
            rows = s.execute(
                select(NotableHolderRow)
                .where(
                    NotableHolderRow.ticker   == ticker,
                    NotableHolderRow.filed_at <= as_of,
                    NotableHolderRow.filed_at >  lower,
                )
                .order_by(NotableHolderRow.filed_at.desc())
            ).scalars().all()

            return [
                NotableHolder.model_validate(r, from_attributes=True)
                for r in rows
            ]
