"""One-time cache fill from live providers.

Idempotent: if a ``cache_runs`` row already exists with ``status='ok'`` for a
``(window_key, ticker, domain)`` triple, the corresponding fetch is skipped.
Failed runs (``status='error'``) and absent rows are always retried.

``cache_runs`` is a *current-status ledger* — one row per
``(window_key, ticker, domain)`` triple, not an append-only log.  Each fetch
attempt supersedes (replaces) any prior row for that triple.  See
``_fetch_one`` for the delete-then-insert strategy.

Adaptation note — domain names vs Phase B store:
    The plan used ``market_meta`` / ``write_market_meta``, but the real
    ``CachedDataStore`` (adapted during Phase B) uses ``company_ratios`` /
    ``write_company_ratios`` so column names mirror ``CompanyRatios`` exactly.
    The map below reflects the real method names.

    ``social_sentiment`` is intentionally absent — no historical data source
    is available for backfill.
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
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
        refetch_domains: set[str] | None = None,
    ) -> None:
        """Wire the fetcher.

        ``refetch_domains`` (default empty) names domains whose existing
        ``status='ok'`` rows are ignored — useful after a provider swap or
        when the user passes ``--refetch-domain news`` on the CLI.
        """
        self._store              = store
        self._window_key         = window_key
        self._window             = window
        self._watchlist          = watchlist
        self._provider_fns       = provider_fns
        self._live_for_domain    = live_providers_for_domain
        self._refetch_domains    = refetch_domains or set()

    # ── public API ─────────────────────────────────────────────────────────────

    async def run(self) -> None:
        """Walk every (ticker, domain) pair and fetch, skipping already-ok rows.

        For each combination that is not already recorded as ``status='ok'``
        in ``cache_runs``, calls the provider and writes the result into the
        store.  The ``cache_runs`` audit row for each triple is replaced on
        every attempt (delete-then-insert in a single transaction), so the
        table is a current-status ledger rather than an append-only log.

        After all fetches complete, drains the store's skipped-write counter
        and writes ``fill_audit.json`` beside the cache file if any rows were
        dropped due to ``MISSING_TIMESTAMP``.  The audit file is only written
        when shrinkage actually occurred — a clean fill leaves no file.
        """
        for ticker in self._watchlist:
            for domain, fn in self._provider_fns.items():
                if self._already_ok(ticker, domain):
                    logger.info("skip %s/%s — already cached as ok", ticker, domain)
                    continue

                await self._fetch_one(ticker, domain, fn)

        # ── shrinkage audit ───────────────────────────────────────────────────
        # Surface any rows dropped at write-time due to MISSING_TIMESTAMP.
        # A non-empty value here means the upstream provider's payload is
        # losing rows silently; investigate before treating the fill as
        # authoritative for a backtest.
        skipped = self._store.drain_skipped_writes()

        if skipped:
            # Resolve a sensible path for the audit file: sit beside the
            # SQLite file so it is easy to find alongside the cache.
            db_path = self._store._engine.url.database
            audit_path = Path(db_path).parent / "fill_audit.json"
            audit_path.write_text(
                json.dumps(
                    {
                        "window": self._window_key,
                        "wrote_at": datetime.now(tz=UTC).isoformat(),
                        "writes_skipped_missing_ts": skipped,
                    },
                    indent=2,
                )
            )
            logger.warning(
                "fetcher: %d row(s) dropped due to MISSING_TIMESTAMP — "
                "see %s",
                sum(skipped.values()),
                audit_path,
            )

    # ── internals ──────────────────────────────────────────────────────────────

    def _already_ok(self, ticker: str, domain: str) -> bool:
        """Return ``True`` iff a prior fetch for this triple has ``status='ok'``
        **and** was written by the currently-configured ``source_provider``.

        Including ``source_provider`` in the predicate means a ``config/data.json``
        flip from e.g. ``finnhub`` to ``tiingo`` invalidates the skip — the new
        provider is re-invoked rather than returning stale rows from the old one.

        Domains listed in ``self._refetch_domains`` are never skipped, regardless
        of the row's provider.

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
            ``(window_key, ticker, domain, source_provider)`` with
            ``status='ok'`` **and** the domain is not flagged for refetch.
        """
        if domain in self._refetch_domains:
            return False

        expected_provider = self._live_for_domain.get(domain)

        with Session(self._store._engine) as s:
            row = s.execute(
                select(CacheRunRow).where(
                    CacheRunRow.window_key      == self._window_key,
                    CacheRunRow.ticker          == ticker,
                    CacheRunRow.domain          == domain,
                    CacheRunRow.source_provider == expected_provider,
                    CacheRunRow.status          == "ok",
                )
            ).scalar_one_or_none()

            return row is not None

    async def _fetch_one(
        self,
        ticker: str,
        domain: str,
        fn: Callable[..., Awaitable[Any]],
    ) -> None:
        """Fetch one (ticker, domain) combo and persist both the data and the audit row.

        Calls ``fn(ticker, start=window.start, end=window.end)``, then
        dispatches to the matching ``CachedDataStore`` writer.  Any exception
        is caught, logged, and recorded as ``status='error'`` — the fetcher
        continues with the next combination rather than aborting.

        The ``cache_runs`` entry for ``(window_key, ticker, domain)`` is
        treated as a *current-status ledger cell*: any prior rows for the
        triple are deleted **within the same transaction** as the new insert,
        so there is never a window where the triple has no row, and stale
        error or ok+0 rows can never survive alongside a newer ok row.  This
        supersede-on-write pattern means the table always has at most one row
        per triple, and the audit script never sees phantom errors from
        previous run attempts.

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

        # Per-(ticker, domain) progress — emitted before the (potentially
        # multi-second) network call so the user can see what's currently
        # being fetched, not just what already finished.
        logger.info("fetching %s/%s …", ticker, domain)

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

            # Success line — explicit row count + elapsed seconds so the
            # user can spot the slow domains at a glance during a long fill.
            elapsed = (datetime.now(tz=UTC) - started).total_seconds()
            logger.info(
                "done %s/%s — %d row(s) in %.1fs",
                ticker, domain, rows_written, elapsed,
            )

        except Exception as exc:
            status = "error"
            error  = repr(exc)
            logger.exception("fetch failed for %s/%s: %s", ticker, domain, exc)

        # Always write the audit row so the next run knows what happened.
        #
        # Supersede semantics: delete ALL prior rows for this triple first,
        # then insert the new one — both operations in the same transaction so
        # there is never a moment where the triple is absent from the ledger.
        # This prevents a transient error row (or a stale ok+0 row after a
        # provider swap) from surviving next to the later, authoritative row
        # and causing the audit script to nag indefinitely.
        with Session(self._store._engine) as s:
            # Remove every prior row for this (window_key, ticker, domain),
            # regardless of status or source_provider — the latest attempt is
            # always the truth, even when the provider has been changed.
            s.execute(
                delete(CacheRunRow).where(
                    CacheRunRow.window_key == self._window_key,
                    CacheRunRow.ticker     == ticker,
                    CacheRunRow.domain     == domain,
                )
            )

            # Insert the fresh row.
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
