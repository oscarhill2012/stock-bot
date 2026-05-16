"""Cache store façade — read/write keyed on (ticker, as_of, domain).

Readers honour the point-in-time filter: rows whose canonical timestamp is
after the supplied ``as_of`` are never returned.  Writers are idempotent on
the primary key — re-running the fetcher is safe (INSERT OR IGNORE semantics
via SQLAlchemy's SQLite dialect).

PIT filter rules (enforced throughout):
- News: filter on ``published_at`` (publication date).
- Filings: filter on ``filed_at`` (SEC filing date).
- Insider trades: filter on ``filed_at`` (Form 4 filing date, NOT
  ``transaction_date`` — trades can predate their filing by days).
- Politician trades: filter on ``COALESCE(disclosure_date, transaction_date)``
  (STOCK Act disclosure timestamp, NOT ``transaction_date``).
- Notable holders: filter on ``filed_at`` (13D/13G filing date).
- Company ratios: latest row with ``as_of_date <= as_of.date()``.
- OHLCV: plain date range (``[start, end]`` inclusive).

Adaptation notes (vs plan's original store.py):
- ``write_ohlcv`` / ``read_ohlcv`` use ``ts`` (DateTime) not ``date`` (Date);
  range queries use SQLite's ``date(ts)`` function.
- ``write_company_ratios`` / ``read_company_ratios`` replace the plan's
  ``write_market_meta`` / ``read_market_meta``; table is ``company_ratios``.
- ``InsiderTrade`` has ``extra="forbid"`` — ``model_validate(row,
  from_attributes=True)`` would raise on the extra ``accession_no`` / ``row_idx``
  columns.  Rows are mapped to model instances explicitly.
- ``OHLCBar.timestamp`` ≠ row column ``ts`` — explicit mapping required.
- ``PoliticianTrade.ticker`` is a model field carried on the row directly.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from backtest.cache.schema import (
    SCHEMA_VERSION,
    CompanyRatiosRow,
    FilingRow,
    InsiderTradeRow,
    MetaRow,
    NewsArticleRow,
    NotableHolderRow,
    OHLCVBarRow,
    PoliticianTradeRow,
    create_all,
)
from data.models import (
    CompanyRatios,
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
)
from data.models.missing import is_missing_timestamp

logger = logging.getLogger(__name__)


def _promote_date_only(value: date | datetime) -> datetime:
    """Promote a date-only value to ``next_trading_day @ 00:00 UTC``.

    Conservative rule: if the row has no intraday time, the cache assumes
    the disclosure could have been made any time during ``value`` and
    therefore only becomes "publicly knowable" at the next-*trading*-day
    open.  Already-timestamped datetimes pass through unchanged.

    Weekends *and* NYSE holidays are skipped, so a disclosure filed on
    Christmas Eve (Wednesday) is promoted to the next market-open day, not
    just the next weekday.

    Parameters
    ----------
    value:
        Either a ``date`` (date-only) or a ``datetime`` (full timestamp).

    Returns
    -------
    datetime
        A timezone-aware UTC datetime whose date is the first NYSE trading
        day strictly after ``value``.
    """
    # isinstance check must come BEFORE date because datetime is a subclass of date.
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    # Pure ``date`` — bump to next NYSE trading day @ 00:00 UTC.
    # Lazy import to avoid widening the import surface for live code paths
    # that never touch this function.
    import pandas_market_calendars as mcal  # noqa: PLC0415

    nyse = mcal.get_calendar("NYSE")

    # Look ahead up to 10 calendar days — enough to cover any holiday run.
    nxt = value + timedelta(days=1)
    for _ in range(10):
        # valid_days returns a DatetimeIndex; an empty result means no session.
        sessions = nyse.valid_days(start_date=nxt, end_date=nxt)
        if len(sessions) > 0:
            return datetime(nxt.year, nxt.month, nxt.day, tzinfo=UTC)
        nxt += timedelta(days=1)

    # Fallback: should be unreachable under any realistic calendar, but avoids
    # an infinite loop if the calendar data is missing or corrupted.
    raise RuntimeError(
        f"_promote_date_only: could not find a NYSE trading day within "
        f"10 calendar days of {value!r}.  Check pandas_market_calendars data."
    )


class CachedDataStore:
    """SQLite-backed read/write façade over the golden backtest cache.

    Methods are grouped by domain; every reader applies the point-in-time
    filter required for lookahead-free backtests.

    Parameters
    ----------
    path:
        Filesystem path to the SQLite file.  Created (including parent
        directories) if it does not already exist.
    """

    def __init__(self, path: Path) -> None:
        """Open (or create) the SQLite file at ``path``; initialise schema."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self._engine = create_engine(f"sqlite:///{path}", future=True)
        create_all(self._engine)
        self._ensure_meta()

        # Per-domain count of rows dropped at write-time because their
        # primary timestamp is MISSING_TIMESTAMP.  Drained by the fetcher
        # to surface shrinkage in fill_audit.json (Phase 7 B3).
        self._writes_skipped_missing_ts: dict[str, int] = {}

    # ── schema version ────────────────────────────────────────────────────────

    def _ensure_meta(self) -> None:
        """Insert the schema-version row if new, or raise if versions diverge.

        Two outcomes:
        - Cache file is brand-new (meta table empty): insert the current
          ``SCHEMA_VERSION`` row and continue.
        - Cache file already has a meta row: compare its ``schema_version``
          against the running code's ``SCHEMA_VERSION``.  Any mismatch is a
          hard error — a stale v1 file flowing through v2 code would produce
          the old leaky semantics with no warning.

        Raises
        ------
        RuntimeError
            If the cached schema version does not match ``SCHEMA_VERSION``.
            The error message names both versions and points the user to the
            refetch command.
        """
        with Session(self._engine) as s:
            existing = s.execute(select(MetaRow)).scalar_one_or_none()

            if existing is None:
                # Fresh cache — stamp it with the current schema version.
                s.add(MetaRow(
                    schema_version=SCHEMA_VERSION,
                    created_at=datetime.now(tz=UTC),
                ))
                s.commit()
                return

            # Existing cache — enforce version agreement.
            if existing.schema_version != SCHEMA_VERSION:
                raise RuntimeError(
                    f"Cache schema version mismatch: file has v{existing.schema_version}, "
                    f"code expects v{SCHEMA_VERSION}.  "
                    f"Re-fill this domain with: "
                    f"python -m scripts.backtest_fetch --refetch-domain <domain>"
                )

    # ── skipped-write counter ─────────────────────────────────────────────────

    def drain_skipped_writes(self) -> dict[str, int]:
        """Return the per-domain skipped-write counts and reset to empty.

        Each value is the number of rows handed to ``write_<domain>``
        whose canonical timestamp was ``MISSING_TIMESTAMP`` and which
        were therefore dropped before persistence.  Called once per fill
        by the fetcher; returns ``{}`` if no skips occurred.

        Returns
        -------
        dict[str, int]
            Per-domain skip counts; empty after this call returns.
        """
        counts = dict(self._writes_skipped_missing_ts)
        self._writes_skipped_missing_ts.clear()
        return counts

    # ── OHLCV ─────────────────────────────────────────────────────────────────

    def write_ohlcv(self, ticker: str, bars: list[OHLCBar]) -> None:
        """Upsert daily OHLCV bars for ``ticker``.

        ``OHLCBar.timestamp`` is stored in the ``ts`` column (DateTime).  The
        bar model does not carry a ``ticker`` field, so the caller must supply
        it explicitly.

        Parameters
        ----------
        ticker:
            Ticker symbol (e.g. ``"AAPL"``).
        bars:
            List of ``OHLCBar`` instances to persist.
        """
        with Session(self._engine) as s:
            for b in bars:
                stmt = sqlite_insert(OHLCVBarRow).values(
                    ticker=ticker,
                    ts=b.timestamp,
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                ).on_conflict_do_nothing(index_elements=["ticker", "ts"])
                s.execute(stmt)
            s.commit()

    def read_ohlcv(
        self, ticker: str, start: date, end: date,
    ) -> list[OHLCBar]:
        """Return bars in ``[start, end]`` inclusive, sorted ascending by date.

        Range comparison uses SQLite's ``date(ts)`` so that a midnight-UTC
        DateTime column is compared correctly against bare date strings.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        start:
            Inclusive lower bound (date).
        end:
            Inclusive upper bound (date).

        Returns
        -------
        list[OHLCBar]
            Bars whose ``ts`` falls on a date in ``[start, end]``, ascending.
        """
        start_str = start.isoformat()
        end_str   = end.isoformat()

        with Session(self._engine) as s:
            rows = s.execute(
                select(OHLCVBarRow)
                .where(
                    OHLCVBarRow.ticker == ticker,
                    # SQLite date() strips the time component for comparison.
                    func.date(OHLCVBarRow.ts) >= start_str,
                    func.date(OHLCVBarRow.ts) <= end_str,
                )
                .order_by(OHLCVBarRow.ts)
            ).scalars().all()

            # OHLCBar.timestamp ≠ row.ts in name — map explicitly.
            bars = [
                OHLCBar(
                    timestamp=row.ts,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    volume=row.volume,
                )
                for row in rows
            ]
            self._audit_record("price_history", ticker, bars)
            return bars

    # ── company ratios ────────────────────────────────────────────────────────

    def write_company_ratios(
        self,
        ticker: str,
        snapshot: CompanyRatios,
        as_of_date: date,
    ) -> None:
        """Upsert one ``CompanyRatios`` snapshot for ``(ticker, as_of_date)``.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        snapshot:
            ``CompanyRatios`` instance to persist.
        as_of_date:
            The date this snapshot was captured (used as the PIT key for
            subsequent reads).
        """
        with Session(self._engine) as s:
            stmt = sqlite_insert(CompanyRatiosRow).values(
                ticker=ticker,
                as_of_date=as_of_date,
                long_name=snapshot.long_name,
                sector=snapshot.sector,
                market_cap=snapshot.market_cap,
                trailing_pe=snapshot.trailing_pe,
                forward_pe=snapshot.forward_pe,
                beta=snapshot.beta,
                dividend_yield=snapshot.dividend_yield,
                fifty_day_average=snapshot.fifty_day_average,
                two_hundred_day_average=snapshot.two_hundred_day_average,
                last_price=snapshot.last_price,
            ).on_conflict_do_nothing(index_elements=["ticker", "as_of_date"])
            s.execute(stmt)
            s.commit()

    def read_company_ratios(
        self, ticker: str, as_of: datetime,
    ) -> CompanyRatios | None:
        """Return the latest ``CompanyRatios`` snapshot with ``as_of_date <= as_of.date()``.

        Returns ``None`` if no snapshot exists for ``ticker`` before ``as_of``.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Point-in-time boundary (only snapshots captured on or before this
            date are considered).
        """
        with Session(self._engine) as s:
            row = s.execute(
                select(CompanyRatiosRow)
                .where(
                    CompanyRatiosRow.ticker      == ticker,
                    CompanyRatiosRow.as_of_date  <= as_of.date(),
                )
                .order_by(CompanyRatiosRow.as_of_date.desc())
                .limit(1)
            ).scalar_one_or_none()

            if row is None:
                return None

            # ``as_of_date`` is not a CompanyRatios field — excluded implicitly
            # because CompanyRatios doesn't forbid extra attributes (Pydantic
            # ignores unknown source attributes in from_attributes mode).
            ratios = CompanyRatios.model_validate(row, from_attributes=True)
            self._audit_record("company_ratios", ticker, [ratios])
            return ratios

    # ── news ──────────────────────────────────────────────────────────────────

    def write_news(self, ticker: str, articles: list[NewsArticle]) -> None:
        """Upsert news articles for ``ticker``.

        Rows whose ``published_at`` is :data:`~data.models.missing.MISSING_TIMESTAMP`
        are skipped with a structured log line so the audit layer can surface
        the count of unstamped upstream rows.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        articles:
            List of ``NewsArticle`` instances to persist.
        """
        with Session(self._engine) as s:
            for a in articles:
                if is_missing_timestamp(a.published_at):
                    logger.warning(
                        "store.write_news: skipping row with missing timestamp "
                        "(ticker=%s, url=%s, source=%s)",
                        ticker, a.url, a.source,
                    )
                    self._writes_skipped_missing_ts["news"] = (
                        self._writes_skipped_missing_ts.get("news", 0) + 1
                    )
                    continue

                stmt = sqlite_insert(NewsArticleRow).values(
                    ticker=ticker,
                    url=a.url,
                    headline=a.headline,
                    summary=a.summary,
                    source=a.source,
                    published_at=a.published_at,
                    sentiment=a.sentiment,
                ).on_conflict_do_nothing(index_elements=["ticker", "url"])
                s.execute(stmt)
            s.commit()

    def read_news(
        self, ticker: str, as_of: datetime, lookback_days: int = 30,
    ) -> list[NewsArticle]:
        """Return articles in ``(as_of - lookback_days, as_of]``, descending.

        PIT filter: ``published_at <= as_of``.  Articles published after
        ``as_of`` are excluded even if they exist in the cache.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Upper bound (inclusive) on ``published_at``.
        lookback_days:
            How many calendar days back to look from ``as_of``.

        Returns
        -------
        list[NewsArticle]
            Matching articles, most-recent first.
        """
        lower = as_of - timedelta(days=lookback_days)

        with Session(self._engine) as s:
            rows = s.execute(
                select(NewsArticleRow)
                .where(
                    NewsArticleRow.ticker       == ticker,
                    NewsArticleRow.published_at <= as_of,
                    NewsArticleRow.published_at >  lower,
                )
                .order_by(NewsArticleRow.published_at.desc())
            ).scalars().all()

            articles = [
                NewsArticle.model_validate(r, from_attributes=True)
                for r in rows
            ]
            self._audit_record("news", ticker, articles)
            return articles

    # ── filings ───────────────────────────────────────────────────────────────

    def write_filings(self, ticker: str, filings: list[Filing]) -> None:
        """Upsert SEC filings for ``ticker``.

        ``Filing.ticker`` carries the ticker, but it is also stored in the row
        to allow the ``WHERE ticker = ?`` index scan.

        Rows whose ``filed_at`` is :data:`~data.models.missing.MISSING_TIMESTAMP`
        are skipped with a structured log line so the audit layer can surface
        the count of unstamped upstream rows.

        Parameters
        ----------
        ticker:
            Ticker symbol (redundant with ``Filing.ticker`` but kept for
            symmetry with other write methods).
        filings:
            List of ``Filing`` instances to persist.
        """
        with Session(self._engine) as s:
            for f in filings:
                if is_missing_timestamp(f.filed_at):
                    logger.warning(
                        "store.write_filings: skipping row with missing timestamp "
                        "(ticker=%s, accession_no=%s, form_type=%s)",
                        ticker, f.accession_no, f.form_type,
                    )
                    self._writes_skipped_missing_ts["filings"] = (
                        self._writes_skipped_missing_ts.get("filings", 0) + 1
                    )
                    continue

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
        self, ticker: str, as_of: datetime, lookback_days: int = 365,
    ) -> list[Filing]:
        """Return filings with ``filed_at <= as_of`` within the lookback window.

        PIT filter: ``filed_at``.  SEC filing date, not the period the filing
        covers (which can be months in the past).

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Upper bound (inclusive) on ``filed_at``.
        lookback_days:
            How many calendar days back to look.

        Returns
        -------
        list[Filing]
            Matching filings, most-recently-filed first.
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

            filings = [Filing.model_validate(r, from_attributes=True) for r in rows]
            self._audit_record("filings", ticker, filings)
            return filings

    # ── insider trades ────────────────────────────────────────────────────────

    def write_insider_trades(
        self, ticker: str, trades: list[InsiderTrade],
    ) -> None:
        """Upsert Form 4 insider trades for ``ticker``.

        ``InsiderTrade`` does not carry an ``accession_no`` field (that lives
        on ``Form4Bundle``).  A synthetic SHA-1 of identifying fields
        ``(ticker, insider_name, transaction_date, side, shares, filed_at)`` is
        used as the surrogate PK so writes are idempotent across re-runs.

        Rows whose ``filed_at`` is :data:`~data.models.missing.MISSING_TIMESTAMP`
        are skipped with a structured log line so the audit layer can surface
        the count of unstamped upstream rows.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        trades:
            List of ``InsiderTrade`` instances to persist.
        """
        with Session(self._engine) as s:
            for t in trades:
                if is_missing_timestamp(t.filed_at):
                    logger.warning(
                        "store.write_insider_trades: skipping row with missing "
                        "timestamp (ticker=%s, insider=%s, transaction_date=%s)",
                        ticker, t.insider_name, t.transaction_date,
                    )
                    self._writes_skipped_missing_ts["insider_trades"] = (
                        self._writes_skipped_missing_ts.get("insider_trades", 0) + 1
                    )
                    continue

                key = "|".join([
                    ticker,
                    t.insider_name,
                    str(t.transaction_date),
                    t.side,
                    str(t.shares),
                    t.filed_at.isoformat(),
                ])
                row_hash = hashlib.sha1(key.encode()).hexdigest()

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
                    # Phase-5 narrative supplement.
                    transaction_code=t.transaction_code,
                    is_10b5_1=t.is_10b5_1,
                    footnote=t.footnote,
                ).on_conflict_do_nothing(index_elements=["row_hash"])
                s.execute(stmt)
            s.commit()

    def read_insider_trades(
        self, ticker: str, as_of: datetime, lookback_days: int = 90,
    ) -> list[InsiderTrade]:
        """Return insider trades filtered by ``filed_at`` — never ``transaction_date``.

        Form 4 trades can be transacted days before they are filed.  Filtering
        on ``transaction_date`` would expose future-filed data to a backtest
        running at ``as_of``, introducing lookahead bias.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Upper bound (inclusive) on ``filed_at``.
        lookback_days:
            How many calendar days back to look from ``as_of``.

        Returns
        -------
        list[InsiderTrade]
            Matching trades, most-recently-filed first.
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

            # InsiderTrade has extra="forbid" — construct explicitly to avoid
            # Pydantic rejecting the row's extra column (row_hash).
            trades = [
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
                    is_10b5_1=row.is_10b5_1 or False,
                    footnote=row.footnote,
                )
                for row in rows
            ]
            self._audit_record("insider_trades", ticker, trades)
            return trades

    # ── politician trades ─────────────────────────────────────────────────────

    def write_politician_trades(
        self, ticker: str, trades: list[PoliticianTrade],
    ) -> None:
        """Upsert politician trades for ``ticker``.

        PK is a synthetic SHA-1 of ``(ticker, politician, transaction_date,
        side, amount_min_usd, amount_max_usd)`` because the upstream feed has
        no natural identifier.

        Date-only upstream values (no intraday time) are stored as midnight
        UTC of the *next business day*.  This conservative promotion prevents
        same-day leakage because the STOCK Act allows disclosure any time of
        day on the recorded disclosure_date.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        trades:
            List of ``PoliticianTrade`` instances to persist.
        """
        with Session(self._engine) as s:
            for t in trades:
                disc_dt = _promote_date_only(t.disclosure_date) if t.disclosure_date else None
                txn_dt  = _promote_date_only(t.transaction_date)

                key = "|".join([
                    ticker,
                    t.politician,
                    str(txn_dt),
                    t.side,
                    str(t.amount_min_usd),
                    str(t.amount_max_usd),
                ])
                row_hash = hashlib.sha1(key.encode()).hexdigest()

                stmt = sqlite_insert(PoliticianTradeRow).values(
                    row_hash=row_hash,
                    ticker=ticker,
                    politician=t.politician,
                    chamber=t.chamber,
                    party=t.party,
                    side=t.side,
                    transaction_date=txn_dt,
                    disclosure_date=disc_dt,
                    amount_min_usd=t.amount_min_usd,
                    amount_max_usd=t.amount_max_usd,
                ).on_conflict_do_nothing(index_elements=["row_hash"])
                s.execute(stmt)
            s.commit()

    def read_politician_trades(
        self, ticker: str, as_of: datetime, lookback_days: int = 90,
    ) -> list[PoliticianTrade]:
        """Return politician trades by ``COALESCE(disclosure_date, transaction_date)``.

        The STOCK Act gives US lawmakers up to 45 days between a trade and its
        public disclosure.  Using ``disclosure_date`` as the PIT filter ensures
        a backtest at ``as_of`` only sees trades the public could actually have
        known about.  When ``disclosure_date`` is NULL, ``transaction_date`` is
        used as the fallback (conservative — likely an older record without
        disclosure metadata).

        Comparison is on full ``DateTime`` values — a 16:00 disclosure on
        day D is invisible at the 09:30 same-day open.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Upper bound (inclusive) on the PIT datetime.
        lookback_days:
            How many calendar days back to look.

        Returns
        -------
        list[PoliticianTrade]
            Matching trades, most-recent by PIT datetime first.
        """
        lower = as_of - timedelta(days=lookback_days)

        pit = func.coalesce(
            PoliticianTradeRow.disclosure_date,
            PoliticianTradeRow.transaction_date,
        )

        with Session(self._engine) as s:
            rows = s.execute(
                select(PoliticianTradeRow)
                .where(
                    PoliticianTradeRow.ticker == ticker,
                    pit <= as_of,
                    pit >  lower,
                )
                .order_by(pit.desc())
            ).scalars().all()

            # row_hash is not a PoliticianTrade field — from_attributes works
            # because PoliticianTrade doesn't have extra="forbid".
            pol_trades = [
                PoliticianTrade.model_validate(r, from_attributes=True)
                for r in rows
            ]
            self._audit_record("politician_trades", ticker, pol_trades)
            return pol_trades

    # ── notable holders ───────────────────────────────────────────────────────

    def write_notable_holders(
        self, ticker: str, holders: list[NotableHolder],
    ) -> None:
        """Upsert SC 13D / 13G / 13F filings for ``ticker``.

        Rows whose ``filed_at`` is :data:`~data.models.missing.MISSING_TIMESTAMP`
        are skipped with a structured log line so the audit layer can surface
        the count of unstamped upstream rows.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        holders:
            List of ``NotableHolder`` instances to persist.
        """
        with Session(self._engine) as s:
            for h in holders:
                if is_missing_timestamp(h.filed_at):
                    logger.warning(
                        "store.write_notable_holders: skipping row with missing "
                        "timestamp (ticker=%s, holder=%s, accession_no=%s)",
                        ticker, h.holder, h.accession_no,
                    )
                    self._writes_skipped_missing_ts["notable_holders"] = (
                        self._writes_skipped_missing_ts.get("notable_holders", 0) + 1
                    )
                    continue

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
        self, ticker: str, as_of: datetime, lookback_days: int = 365,
    ) -> list[NotableHolder]:
        """Return 13D/13G/13F filings with ``filed_at <= as_of``.

        PIT filter: ``filed_at`` (date the SEC received the filing).

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Upper bound (inclusive) on ``filed_at``.
        lookback_days:
            How many calendar days back to look.

        Returns
        -------
        list[NotableHolder]
            Matching filings, most-recently-filed first.
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

            holders = [
                NotableHolder.model_validate(r, from_attributes=True)
                for r in rows
            ]
            self._audit_record("notable_holders", ticker, holders)
            return holders

    # ── Audit hook ────────────────────────────────────────────────────────────
    #
    # The driver enables read capture once per tick; every ``read_*`` method
    # appends its rows into ``self._audit_reads``.  At end-of-tick the driver
    # calls ``_audit_drain_reads`` to retrieve and reset the captured set.
    #
    # When capture is disabled (the default — live runs) the methods skip
    # the append for zero overhead.

    def _audit_capture_enabled(self) -> bool:
        """Return ``True`` iff per-tick read capture is currently on."""
        return getattr(self, "_audit_reads", None) is not None

    def _audit_record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        """Append ``rows`` into the per-tick capture if enabled.

        Parameters
        ----------
        domain:
            Domain key (e.g. ``"news"``, ``"price_history"``).
        ticker:
            Ticker symbol.
        rows:
            Model instances returned by the read method.
        """
        if not self._audit_capture_enabled():
            return
        self._audit_reads.setdefault(domain, {}).setdefault(ticker, []).extend(rows)

    def _audit_enable_capture(self) -> None:
        """Begin per-tick read capture.  Idempotent — clears any prior state."""
        self._audit_reads: dict = {}

    def _audit_drain_reads(self) -> dict:
        """Return and reset the per-tick capture log.

        Returns
        -------
        dict
            ``{domain: {ticker: [rows]}}`` — empty when capture was never
            enabled.
        """
        captured = getattr(self, "_audit_reads", {}) or {}
        self._audit_reads = {}
        return captured
