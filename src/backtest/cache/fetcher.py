"""One-time cache fill from live providers.

Idempotent: if a ``cache_runs`` row already exists with ``status='ok'`` for a
``(window_key, ticker, domain)`` triple, the corresponding fetch is skipped.
Failed runs (``status='error'``) and partial runs (no row) are retried.

Adaptation note vs. plan spec
------------------------------
The plan assumes a uniform ``write_X(ticker, results)`` writer signature for
every domain.  In practice ``write_market_meta`` takes three arguments:
``(ticker, snapshot, as_of_date)`` — because a single snapshot is stored with
its observation date rather than as a list.  The ``_write_domain`` helper below
handles this difference without leaking it into the caller.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backtest.cache.schema import CacheRunRow
from backtest.cache.store import CachedDataStore
from backtest.windows import Window

logger = logging.getLogger(__name__)


class Fetcher:
    """Drive a one-time cache fill across (window × watchlist × domain).

    Parameters
    ----------
    store:
        The shared golden cache to write into.
    window_key:
        Era slug (e.g. ``"svb-stress-2023-03"``), used as the
        ``cache_runs.window_key`` discriminator.
    window:
        Resolved date range for this era.
    watchlist:
        Tickers to fetch.
    provider_fns:
        Domain → async fetch function (the *live* provider, not the cache
        provider).  Injected so tests can stub them without real network calls.
        Each function is called as ``fn(ticker, start=window.start,
        end=window.end)``.
    live_providers_for_domain:
        Domain → provider name string, recorded in
        ``cache_runs.source_provider`` for audit purposes.
    """

    def __init__(
        self,
        *,
        store: CachedDataStore,
        window_key: str,
        window: Window,
        watchlist: list[str],
        provider_fns: dict[str, Callable[..., Awaitable[Any]]],
        live_providers_for_domain: dict[str, str],
    ) -> None:
        """Wire the fetcher with everything it needs to fill the cache."""
        self._store            = store
        self._window_key       = window_key
        self._window           = window
        self._watchlist        = watchlist
        self._provider_fns     = provider_fns
        self._live_for_domain  = live_providers_for_domain

    # ── Public interface ──────────────────────────────────────────────────

    async def run(self) -> None:
        """Walk every (ticker, domain) and fetch — skipping completed rows.

        Iteration order is ticker-first so all domains for one ticker are
        fetched in sequence before moving on.  This keeps rate-limit bursts
        per-ticker rather than per-domain, which is friendlier to APIs that
        throttle by symbol.
        """
        for ticker in self._watchlist:
            for domain, fn in self._provider_fns.items():
                if self._already_ok(ticker, domain):
                    logger.info("skip %s/%s — already cached", ticker, domain)
                    continue
                await self._fetch_one(ticker, domain, fn)

    # ── Internal helpers ──────────────────────────────────────────────────

    def _already_ok(self, ticker: str, domain: str) -> bool:
        """Return True iff a prior fetch row exists with ``status='ok'``.

        Parameters
        ----------
        ticker:
            The equity symbol.
        domain:
            The data domain (e.g. ``"ohlcv"``, ``"news"``).

        Returns
        -------
        bool
            ``True`` if the combination has been successfully cached before.
        """
        with Session(self._store._engine) as s:
            row = s.execute(
                select(CacheRunRow)
                .where(
                    CacheRunRow.window_key == self._window_key,
                    CacheRunRow.ticker     == ticker,
                    CacheRunRow.domain     == domain,
                    CacheRunRow.status     == "ok",
                )
            ).scalar_one_or_none()
            return row is not None

    def _write_domain(
        self,
        ticker: str,
        domain: str,
        results: Any,
    ) -> int:
        """Dispatch to the correct store writer and return the number of rows written.

        Most domains use the uniform ``write_X(ticker, items)`` signature.
        ``market_meta`` is the exception — it stores one ``StockStats`` snapshot
        with an explicit ``as_of_date``, so the provider function must return a
        two-tuple ``(snapshot, as_of_date)`` for that domain.

        Parameters
        ----------
        ticker:
            The equity symbol.
        domain:
            The data domain.
        results:
            Whatever the provider function returned.

        Returns
        -------
        int
            Number of items persisted (best-effort; 0 when indeterminate).
        """
        if domain == "market_meta":
            # Provider returns a list of (snapshot, as_of_date) pairs so the
            # caller can handle the "no data" case uniformly with an empty list.
            rows_written = 0
            for snapshot, as_of_date in results:
                self._store.write_market_meta(ticker, snapshot, as_of_date)
                rows_written += 1
            return rows_written

        # All other domains: uniform write_X(ticker, list[Model]).
        writer_map: dict[str, str] = {
            "ohlcv":             "write_ohlcv",
            "news":              "write_news",
            "filings":           "write_filings",
            "insider_trades":    "write_insider_trades",
            "politician_trades": "write_politician_trades",
            "notable_holders":   "write_notable_holders",
        }
        writer_name = writer_map[domain]
        getattr(self._store, writer_name)(ticker, results)
        return len(results) if hasattr(results, "__len__") else 0

    async def _fetch_one(
        self,
        ticker: str,
        domain: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        """Fetch + persist one (ticker, domain) combo; record audit row.

        On success, writes a ``cache_runs`` row with ``status='ok'``.
        On any exception, writes ``status='error'`` and logs the traceback.
        Either way the run continues — a single failure does not abort the
        entire fetch sweep.

        Parameters
        ----------
        ticker:
            The equity symbol.
        domain:
            The data domain.
        fn:
            The async provider function to call.
        """
        started      = datetime.now(tz=UTC)
        run_id       = uuid.uuid4().hex
        status       = "ok"
        error: str | None = None
        rows_written = 0

        try:
            results = await fn(
                ticker,
                start=self._window.start,
                end=self._window.end,
            )
            rows_written = self._write_domain(ticker, domain, results)
            logger.info(
                "fetched %s/%s — %d rows written", ticker, domain, rows_written,
            )

        except Exception as exc:
            status = "error"
            error  = repr(exc)
            logger.exception("fetch failed for %s/%s: %s", ticker, domain, exc)

        # Always write the audit row, even on failure, so subsequent runs know
        # the attempt was made and can use it for diagnostics.
        with Session(self._store._engine) as s:
            s.add(CacheRunRow(
                run_id=run_id,
                started_at=started,
                finished_at=datetime.now(tz=UTC),
                window_key=self._window_key,
                ticker=ticker,
                domain=domain,
                source_provider=self._live_for_domain.get(domain, "unknown"),
                rows_written=rows_written,
                status=status,
                error=error or "",
            ))
            s.commit()
