"""One-time cache fill from live providers.

Idempotent: if a ``cache_runs`` row already exists with ``status='ok'`` for a
``(window_key, ticker, domain)`` triple, the corresponding fetch is skipped.
Failed runs (``status='error'``) and absent rows are always retried.

Adaptation note — domain names vs Phase B store:
    The plan used ``market_meta`` / ``write_market_meta``, but the real
    ``CachedDataStore`` (adapted during Phase B) uses ``company_ratios`` /
    ``write_company_ratios`` so column names mirror ``CompanyRatios`` exactly.
    The map below reflects the real method names.

    ``social_sentiment`` is intentionally absent — no historical data source
    is available for backfill.
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

# ---------------------------------------------------------------------------
# Domain → CachedDataStore writer method name.
#
# The fetcher resolves the writer by attribute look-up so that adding a new
# domain is a one-line change here — no large switch statement.
#
# ``social_sentiment`` is excluded: no historical data source exists for it.
# ``market_meta`` is excluded: the real store calls it ``company_ratios``
# (plan adaptation from Phase B).
# ---------------------------------------------------------------------------
_WRITER_BY_DOMAIN: dict[str, str] = {
    "ohlcv":             "write_ohlcv",
    "company_ratios":    "write_company_ratios",
    "news":              "write_news",
    "filings":           "write_filings",
    "insider_trades":    "write_insider_trades",
    "politician_trades": "write_politician_trades",
    "notable_holders":   "write_notable_holders",
}


class Fetcher:
    """Drive a one-time cache fill across the cartesian product (window × watchlist × domain).

    Parameters
    ----------
    store:
        The shared golden cache to write into.
    window_key:
        Era slug (e.g. ``"svb-stress-2023-03"``); recorded in ``cache_runs``.
    window:
        Resolved ``Window`` with ``start`` and ``end`` dates.
    watchlist:
        List of ticker symbols to fetch.
    provider_fns:
        Mapping of domain → async fetch callable.  Each callable is called as
        ``fn(ticker, start=window.start, end=window.end)`` and must return a
        list (or sequence) of model instances accepted by the matching store
        writer.  Injected so tests can supply stubs without hitting the network.
    live_providers_for_domain:
        Mapping of domain → provider name string, stored verbatim in the
        ``cache_runs.source_provider`` column for auditing.
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
        self._store            = store
        self._window_key       = window_key
        self._window           = window
        self._watchlist        = watchlist
        self._provider_fns     = provider_fns
        self._live_for_domain  = live_providers_for_domain

    # ── public API ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Walk every (ticker, domain) pair and fetch, skipping already-ok rows.

        For each combination that is not already recorded as ``status='ok'``
        in ``cache_runs``, calls the provider and writes the result into the
        store.  An audit row is inserted for every attempted fetch, whether
        it succeeds or fails.
        """
        for ticker in self._watchlist:
            for domain, fn in self._provider_fns.items():
                if self._already_ok(ticker, domain):
                    logger.info("skip %s/%s — already cached as ok", ticker, domain)
                    continue

                await self._fetch_one(ticker, domain, fn)

    # ── internals ──────────────────────────────────────────────────────────────

    def _already_ok(self, ticker: str, domain: str) -> bool:
        """Return ``True`` iff a prior fetch for this triple has ``status='ok'``.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        domain:
            Domain name (e.g. ``"ohlcv"``, ``"news"``).

        Returns
        -------
        bool
            ``True`` when a ``cache_runs`` row exists for
            ``(window_key, ticker, domain)`` with ``status='ok'``.
        """
        with Session(self._store._engine) as s:
            row = s.execute(
                select(CacheRunRow).where(
                    CacheRunRow.window_key == self._window_key,
                    CacheRunRow.ticker     == ticker,
                    CacheRunRow.domain     == domain,
                    CacheRunRow.status     == "ok",
                )
            ).scalar_one_or_none()

            return row is not None

    async def _fetch_one(
        self,
        ticker: str,
        domain: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        """Fetch one (ticker, domain) combo and persist both the data and an audit row.

        Calls ``fn(ticker, start=window.start, end=window.end)``, then
        dispatches to the matching ``CachedDataStore`` writer.  Any exception
        is caught, logged, and recorded as ``status='error'`` — the fetcher
        continues with the next combination rather than aborting.

        Parameters
        ----------
        ticker:
            Ticker symbol.
        domain:
            Domain name; used to look up the writer method in ``_WRITER_BY_DOMAIN``.
        fn:
            Async provider callable for this domain.
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

            # Resolve the writer from the domain map and call it.
            # ``company_ratios`` has a three-argument signature
            # ``write_company_ratios(ticker, snapshot, as_of_date)`` — callers
            # that supply a ``company_ratios`` provider fn must return a
            # list of ``(CompanyRatios, date)`` two-tuples so the fetcher can
            # unpack them correctly.
            writer_name = _WRITER_BY_DOMAIN[domain]
            writer      = getattr(self._store, writer_name)

            if domain == "company_ratios":
                # Provider returns list[(snapshot, as_of_date)] tuples.
                for snapshot, as_of_date in results:
                    writer(ticker, snapshot, as_of_date)
                rows_written = len(results)
            else:
                writer(ticker, results)
                rows_written = len(results) if hasattr(results, "__len__") else 0

        except Exception as exc:
            status = "error"
            error  = repr(exc)
            logger.exception("fetch failed for %s/%s: %s", ticker, domain, exc)

        # Always write the audit row so the next run knows what happened.
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
