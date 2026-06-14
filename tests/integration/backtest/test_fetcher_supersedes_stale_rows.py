"""Tests that the fetcher supersedes (replaces) stale ``cache_runs`` rows, and
that ``--refetch-domain`` genuinely replaces cached data (not a silent no-op).

``cache_runs`` is a *current-status ledger*, one row per
``(window_key, ticker, domain)``.  Each fetch attempt must delete any prior row
for that triple — within the same transaction — before inserting the new one.

Three invariants are verified here:

1. **error-then-ok** — a transient error row is replaced by the later success row;
   exactly one row survives, and it is ``status='ok'``.

2. **ok-then-forced-refetch** — a successful row is replaced when the domain is
   in ``refetch_domains``; exactly one row survives, and it is the newer one
   (distinguished by ``run_id`` and ``rows_written``).

3. **other triples untouched** — rows for a different ticker or domain are not
   disturbed when only the target triple is refetched.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import date as date_type

from backtest.cache.fetcher import Fetcher
from backtest.cache.schema import CacheRunRow
from backtest.cache.store import CachedDataStore
from backtest.windows import Window
from data.models import CompanyRatios, OHLCBar

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_WINDOW_KEY = "supersede-test"

_FAKE_BARS = [
    OHLCBar(
        timestamp=datetime(2023, 3, d, tzinfo=UTC),
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=100.0,
    )
    for d in range(6, 11)  # five bars, one per day
]


def _store(tmp_path: Path) -> CachedDataStore:
    """Create a fresh temporary cache store."""
    return CachedDataStore(tmp_path / "cache.sqlite")


def _window() -> Window:
    """Return a minimal test window (no ``key`` field — passed separately to Fetcher)."""
    return Window(start=date(2023, 3, 6), end=date(2023, 3, 10), notes="", risk_free_rate_annual=0.048)


def _count_cache_runs(
    store: CachedDataStore,
    ticker: str,
    domain: str,
) -> list[CacheRunRow]:
    """Return every ``cache_runs`` row for the given (window_key, ticker, domain)."""
    with Session(store._engine) as s:
        rows = s.execute(
            select(CacheRunRow).where(
                CacheRunRow.window_key == _WINDOW_KEY,
                CacheRunRow.ticker    == ticker,
                CacheRunRow.domain    == domain,
            )
        ).scalars().all()

        # Detach from the session so callers can inspect after the session closes.
        return list(rows)


# ---------------------------------------------------------------------------
# Test 1: error-then-ok  (the bug that prompted this fix)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_row_superseded_by_later_ok(tmp_path: Path) -> None:
    """A transient-error row must be replaced when the same triple is retried successfully.

    Scenario mirrors AVGO news: first attempt raises (Finnhub timeout), which
    writes a ``status='error'`` row.  A second run with a working provider
    succeeds; exactly one row must remain for the triple, and it must be the
    ``status='ok'`` row.

    Without the fix, two rows exist (one error, one ok) and the audit script
    flags the window indefinitely.
    """
    store  = _store(tmp_path)
    window = _window()

    call_count = 0

    async def failing_provider(ticker: str, *, start: date, end: date) -> list:
        """First call raises; subsequent calls return real data."""
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise TimeoutError("Finnhub timed out (simulated)")
        return _FAKE_BARS

    # --- First run: provider raises, row recorded as error ---
    fetcher_first = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AVGO"],
        provider_fns={"ohlcv": failing_provider},
        live_providers_for_domain={"ohlcv": "finnhub"},
    )
    await fetcher_first.run()

    rows_after_error = _count_cache_runs(store, "AVGO", "ohlcv")
    assert len(rows_after_error) == 1, "expected exactly one row after the error run"
    assert rows_after_error[0].status == "error"

    # --- Second run: provider succeeds; error row must be superseded ---
    # An errored row is never skipped (_already_ok returns False for non-ok),
    # so a plain second run() call retries.
    fetcher_second = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AVGO"],
        provider_fns={"ohlcv": failing_provider},
        live_providers_for_domain={"ohlcv": "finnhub"},
    )
    await fetcher_second.run()

    rows_after_ok = _count_cache_runs(store, "AVGO", "ohlcv")

    # The key assertion: ledger must hold exactly one row — the ok one.
    assert len(rows_after_ok) == 1, (
        f"expected exactly 1 cache_runs row for AVGO/ohlcv, found {len(rows_after_ok)}: "
        f"{[(r.run_id, r.status) for r in rows_after_ok]}"
    )
    assert rows_after_ok[0].status == "ok", (
        f"surviving row must be status='ok', got {rows_after_ok[0].status!r}"
    )
    assert rows_after_ok[0].rows_written == len(_FAKE_BARS), (
        "surviving row must record the correct rows_written count"
    )


# ---------------------------------------------------------------------------
# Test 2: ok-then-forced-refetch
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ok_row_superseded_by_forced_refetch(tmp_path: Path) -> None:
    """A forced refetch (domain in ``refetch_domains``) must replace the prior ok row.

    After a forced refetch, exactly one row must survive for the triple, and it
    must be the newer one — verified by comparing ``run_id`` values (each
    ``_fetch_one`` call mints a fresh ``uuid.uuid4().hex``).
    """
    store  = _store(tmp_path)
    window = _window()

    # Both runs return the same bars — content doesn't matter here.
    async def working_provider(ticker: str, *, start: date, end: date) -> list:
        return _FAKE_BARS

    # --- First run: successful fill ---
    fetcher_first = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": working_provider},
        live_providers_for_domain={"ohlcv": "yfinance"},
    )
    await fetcher_first.run()

    rows_after_first = _count_cache_runs(store, "AAPL", "ohlcv")
    assert len(rows_after_first) == 1, "expected exactly one row after first run"
    first_run_id = rows_after_first[0].run_id

    # --- Second run: forced refetch overrides the skip ---
    fetcher_second = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"ohlcv": working_provider},
        live_providers_for_domain={"ohlcv": "yfinance"},
        refetch_domains={"ohlcv"},
    )
    await fetcher_second.run()

    rows_after_second = _count_cache_runs(store, "AAPL", "ohlcv")

    # Ledger must still hold exactly one row — the new one.
    assert len(rows_after_second) == 1, (
        f"expected exactly 1 cache_runs row for AAPL/ohlcv after refetch, "
        f"found {len(rows_after_second)}: {[(r.run_id, r.status) for r in rows_after_second]}"
    )

    second_run_id = rows_after_second[0].run_id
    assert second_run_id != first_run_id, (
        "run_id must differ between the two runs — the newer row must have replaced the older one"
    )
    assert rows_after_second[0].rows_written == len(_FAKE_BARS)


# ---------------------------------------------------------------------------
# Test 3: other triples are not disturbed
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_supersede_does_not_touch_other_triples(tmp_path: Path) -> None:
    """Superseding one triple must leave rows for other tickers / domains intact.

    Scenario:
    - Seed a ``status='ok'`` row for MSFT/ohlcv (different ticker).
    - Seed a ``status='ok'`` row for AAPL/news (different domain, same ticker).
    - Run a forced refetch of AAPL/ohlcv only.

    Afterwards, exactly one row must survive for each of the other two triples,
    and the row content must be unchanged.
    """
    store  = _store(tmp_path)
    window = _window()

    # Seed two unrelated rows directly — no provider fn needed.
    with Session(store._engine) as s:
        s.add(CacheRunRow(
            run_id="msft-ohlcv-seed",
            started_at=datetime.now(tz=UTC),
            finished_at=datetime.now(tz=UTC),
            window_key=_WINDOW_KEY,
            ticker="MSFT",
            domain="ohlcv",
            source_provider="yfinance",
            rows_written=5,
            status="ok",
            error="",
        ))
        s.add(CacheRunRow(
            run_id="aapl-news-seed",
            started_at=datetime.now(tz=UTC),
            finished_at=datetime.now(tz=UTC),
            window_key=_WINDOW_KEY,
            ticker="AAPL",
            domain="news",
            source_provider="finnhub",
            rows_written=12,
            status="ok",
            error="",
        ))
        s.commit()

    # Force-refetch only AAPL/ohlcv.
    async def working_ohlcv(ticker: str, *, start: date, end: date) -> list:
        return _FAKE_BARS

    fetcher = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],           # only AAPL — MSFT is not in this run
        provider_fns={"ohlcv": working_ohlcv},
        live_providers_for_domain={"ohlcv": "yfinance"},
        refetch_domains={"ohlcv"},
    )
    await fetcher.run()

    # AAPL/ohlcv: one row, the newly written one.
    aapl_ohlcv = _count_cache_runs(store, "AAPL", "ohlcv")
    assert len(aapl_ohlcv) == 1
    assert aapl_ohlcv[0].status == "ok"

    # MSFT/ohlcv: the seeded row must be untouched.
    msft_ohlcv = _count_cache_runs(store, "MSFT", "ohlcv")
    assert len(msft_ohlcv) == 1, (
        f"MSFT/ohlcv row must not be disturbed; found {len(msft_ohlcv)} rows"
    )
    assert msft_ohlcv[0].run_id == "msft-ohlcv-seed"

    # AAPL/news: the seeded row must be untouched.
    aapl_news = _count_cache_runs(store, "AAPL", "news")
    assert len(aapl_news) == 1, (
        f"AAPL/news row must not be disturbed; found {len(aapl_news)} rows"
    )
    assert aapl_news[0].run_id == "aapl-news-seed"


# ---------------------------------------------------------------------------
# Test 4: refetch genuinely replaces cached data (the original silent-failure bug)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refetch_domain_genuinely_replaces_cached_data(tmp_path: Path) -> None:
    """A forced refetch must replace stale values in the store, not silently drop them.

    Regression test for the bug where ``--refetch-domain company_ratios`` re-ran
    the provider but wrote nothing new because ``on_conflict_do_nothing`` absorbed
    every row — and the audit row still reported ``rows_written=N`` because it
    counted the provider-payload size, not actual DB inserts.

    Scenario:
    1.  Seed a stale ``CompanyRatios`` snapshot via ``write_company_ratios``
        (market_cap=1_000.0 — the "buggy" value).
    2.  Run the fetcher with ``refetch_domains={"company_ratios"}`` and a
        provider that returns the corrected value (market_cap=9_999.0).
    3.  Assert the store now holds the corrected value, not the stale one.
    4.  Assert the ``cache_runs`` row records the actual inserted count (1),
        not the stale-absorbed count (0 without delete-first).
    """
    store  = _store(tmp_path)
    window = _window()

    # ── Step 1: seed stale data ───────────────────────────────────────────────
    stale_snapshot = CompanyRatios(ticker="AAPL", market_cap=1_000.0)
    store.write_company_ratios("AAPL", stale_snapshot, date_type(2023, 3, 6))

    # Confirm the stale value is in the cache.
    as_of = datetime(2023, 3, 10, tzinfo=UTC)
    cached = store.read_company_ratios("AAPL", as_of=as_of)
    assert cached is not None and cached.market_cap == 1_000.0, (
        "stale value should be in the cache before refetch"
    )

    # ── Step 2: run the fetcher with the corrected provider ───────────────────
    corrected_snapshot = CompanyRatios(ticker="AAPL", market_cap=9_999.0)

    # Provider returns list[(snapshot, as_of_date)] tuples (company_ratios convention).
    async def corrected_provider(
        ticker: str, *, start: date_type, end: date_type
    ) -> list:
        return [(corrected_snapshot, date_type(2023, 3, 6))]

    fetcher = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"company_ratios": corrected_provider},
        live_providers_for_domain={"company_ratios": "sec_edgar"},
        refetch_domains={"company_ratios"},
    )
    await fetcher.run()

    # ── Step 3: assert the store now holds the corrected value ────────────────
    refreshed = store.read_company_ratios("AAPL", as_of=as_of)
    assert refreshed is not None, "corrected snapshot must be readable after refetch"
    assert refreshed.market_cap == 9_999.0, (
        f"expected corrected market_cap=9_999.0 after refetch, "
        f"got {refreshed.market_cap} — delete-first did not fire or write failed"
    )

    # ── Step 4: assert the audit row records honest inserted count ────────────
    run_rows = _count_cache_runs(store, "AAPL", "company_ratios")
    assert len(run_rows) == 1, f"expected exactly 1 cache_runs row, got {len(run_rows)}"
    assert run_rows[0].rows_written == 1, (
        f"rows_written must be 1 (actual DB insert), "
        f"got {run_rows[0].rows_written} — provider-payload count must not be used"
    )
    assert run_rows[0].status == "ok"
