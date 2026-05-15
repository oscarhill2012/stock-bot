"""Decorator over ``CachedDataStore`` that captures every row returned.

Used only by the deep-dump audit script (Layer 2) — not in normal runs.
Wrapping the existing store rather than subclassing keeps the contract
explicit: every ``read_*`` method passes through; every returned row is
appended to ``_captured``.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from backtest.cache.store import CachedDataStore


class AuditingStore:
    """Capture every cache-read row, then delegate to the wrapped store.

    Parameters
    ----------
    inner:
        The underlying ``CachedDataStore``.  All writes pass straight
        through; all reads are recorded *and* delegated.
    """

    def __init__(self, *, inner: CachedDataStore) -> None:
        """Initialise the decorator with the wrapped inner store."""
        self._inner    = inner
        self._captured: dict[str, dict[str, list[Any]]] = {}

    # ── pass-through writes ───────────────────────────────────────────────────

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes (writers etc.) to the wrapped store."""
        return getattr(self._inner, name)

    # ── instrumented reads ────────────────────────────────────────────────────

    def _record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        """Append ``rows`` to the per-domain, per-ticker capture buffer.

        Parameters
        ----------
        domain:
            Data domain key (e.g. ``"news"``, ``"price_history"``).
        ticker:
            Ticker symbol the rows belong to.
        rows:
            The row objects to record.
        """
        self._captured.setdefault(domain, {}).setdefault(ticker, []).extend(rows)

    def read_ohlcv(self, ticker: str, start: date, end: date) -> list[Any]:
        """Read OHLCV bars, capture them, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        start:
            Inclusive start date.
        end:
            Inclusive end date.

        Returns
        -------
        list
            OHLCV bar rows, same as the inner store would return.
        """
        rows = self._inner.read_ohlcv(ticker, start, end)
        self._record("price_history", ticker, rows)
        return rows

    def read_news(self, ticker: str, as_of: datetime, lookback_days: int = 7) -> list[Any]:
        """Read news, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling; rows after this are excluded.
        lookback_days:
            How far back to look.

        Returns
        -------
        list
            News article rows.
        """
        rows = self._inner.read_news(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("news", ticker, rows)
        return rows

    def read_filings(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[Any]:
        """Read filings, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling.
        lookback_days:
            How far back to look.

        Returns
        -------
        list
            Filing rows.
        """
        rows = self._inner.read_filings(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("filings", ticker, rows)
        return rows

    def read_insider_trades(self, ticker: str, as_of: datetime, lookback_days: int = 30) -> list[Any]:
        """Read insider trades, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling.
        lookback_days:
            How far back to look.

        Returns
        -------
        list
            Insider trade rows.
        """
        rows = self._inner.read_insider_trades(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("insider_trades", ticker, rows)
        return rows

    def read_notable_holders(self, ticker: str, as_of: datetime) -> list[Any]:
        """Read notable holders, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling.

        Returns
        -------
        list
            Notable holder rows.
        """
        rows = self._inner.read_notable_holders(ticker, as_of=as_of)
        self._record("notable_holders", ticker, rows)
        return rows

    def read_politician_trades(self, ticker: str, as_of: datetime, lookback_days: int = 90) -> list[Any]:
        """Read politician trades, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling.
        lookback_days:
            How far back to look.

        Returns
        -------
        list
            Politician trade rows.
        """
        rows = self._inner.read_politician_trades(ticker, as_of=as_of, lookback_days=lookback_days)
        self._record("politician_trades", ticker, rows)
        return rows

    def read_company_ratios(self, ticker: str, as_of: datetime) -> Any:
        """Read company ratios, capture, return.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        as_of:
            Historical clock ceiling.

        Returns
        -------
        CompanyRatios | None
            The most recent company ratios row at or before ``as_of``,
            or ``None`` if the cache holds no entry for this ticker.
        """
        result = self._inner.read_company_ratios(ticker, as_of=as_of)
        if result is not None:
            self._record("company_ratios", ticker, [result])
        return result

    def drain_captured(self) -> dict[str, dict[str, list[Any]]]:
        """Return and reset the captured rows.

        Returns
        -------
        dict
            Mapping ``{domain: {ticker: [rows]}}`` of everything captured
            since the last drain (or since construction).  The internal
            buffer is cleared so the next call starts fresh.
        """
        out = self._captured
        self._captured = {}
        return out
