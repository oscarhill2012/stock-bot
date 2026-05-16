"""After a provider swap, the fetcher must re-fetch, not blindly skip."""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from backtest.cache.fetcher import Fetcher
from backtest.cache.schema import CacheRunRow
from backtest.cache.store import CachedDataStore
from backtest.windows import Window

# The window key is stored separately from the Window object (Window has no
# ``key`` field — it is the dict key in load_windows and a separate arg to Fetcher).
_WINDOW_KEY = "t"


@pytest.fixture()
def store_and_window(tmp_path: Path) -> tuple[CachedDataStore, Window]:
    """Create a temporary store and a minimal test window."""
    store  = CachedDataStore(tmp_path / "cache.sqlite")
    window = Window(
        start=date(2023, 3, 1),
        end=date(2023, 3, 15),
    )
    return store, window


def _seed_ok_row(store: CachedDataStore, *, provider: str) -> None:
    """Insert a cache_runs row with status='ok' and the supplied provider name."""
    with Session(store._engine) as s:
        s.add(CacheRunRow(
            run_id="r1",
            started_at=datetime.now(tz=UTC),
            finished_at=datetime.now(tz=UTC),
            window_key=_WINDOW_KEY,
            ticker="AAPL",
            domain="news",
            source_provider=provider,
            rows_written=10,
            status="ok",
            error="",
        ))
        s.commit()


@pytest.mark.asyncio
async def test_provider_swap_invalidates_cache_skip(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """After config flips news provider from finnhub → tiingo, _already_ok=False."""
    store, window = store_and_window

    # Pretend a previous fill ran under "finnhub".
    _seed_ok_row(store, provider="finnhub")

    # Build a fetcher that now thinks "tiingo" is the news provider.
    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
    )

    await fetcher.run()

    # The provider flip must trigger a fresh fetch, not skip on the stale row.
    assert called == ["AAPL"]


@pytest.mark.asyncio
async def test_same_provider_still_skipped(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """Same provider as the previous fill must still short-circuit."""
    store, window = store_and_window

    _seed_ok_row(store, provider="tiingo")

    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
    )

    await fetcher.run()

    assert called == []


@pytest.mark.asyncio
async def test_refetch_domain_forces_refill(
    store_and_window: tuple[CachedDataStore, Window],
) -> None:
    """``refetch_domains={'news'}`` overrides the skip even when provider matches."""
    store, window = store_and_window

    _seed_ok_row(store, provider="tiingo")

    called: list[str] = []

    async def fake_news(ticker: str, *, start: date, end: date) -> list:
        called.append(ticker)
        return []

    fetcher = Fetcher(
        store=store,
        window_key=_WINDOW_KEY,
        window=window,
        watchlist=["AAPL"],
        provider_fns={"news": fake_news},
        live_providers_for_domain={"news": "tiingo"},
        refetch_domains={"news"},
    )

    await fetcher.run()

    assert called == ["AAPL"]
