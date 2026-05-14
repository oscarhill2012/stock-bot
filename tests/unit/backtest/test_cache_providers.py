"""Round-trip equivalence tests for every cache provider.

Each test:
1. Writes fixture rows into a fresh in-memory ``CachedDataStore``.
2. Calls the corresponding cache provider's ``fetch`` coroutine.
3. Asserts the returned Pydantic model matches the live-provider contract.

The point-in-time filter is also verified: rows published/filed *after* the
supplied ``as_of`` must not appear in the result.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers import _store_handle
from data.models import (
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    PoliticianTrade,
    StockStats,
)
from data.models.market import OHLCBar


# ── Shared fixture ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _wire_store(tmp_path: Path):
    """Each test gets a fresh per-temp-dir store, cleared after the test.

    Using ``autouse=True`` means every test function in this module gets a
    clean store without having to request the fixture explicitly.  The store
    is also returned so tests that need to write fixture rows can request it
    by name.
    """
    store = CachedDataStore(tmp_path / "store.sqlite")
    _store_handle.set_store(store)
    yield store
    _store_handle.clear_store()


# ── news_cache ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_news_cache_returns_pydantic_articles(_wire_store: CachedDataStore) -> None:
    """``news_cache.fetch`` returns ``list[NewsArticle]`` filtered by ``as_of``."""
    from backtest.providers import news_cache  # noqa: F401 — triggers @register

    _wire_store.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL",
            url="https://example.com/1",
            headline="Test headline",
            summary="Test summary",
            source="TestSource",
            published_at=datetime(2023, 3, 10, tzinfo=UTC),
            sentiment=None,
        ),
    ])

    result = await news_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], NewsArticle)
    assert result[0].ticker == "AAPL"
    assert result[0].url == "https://example.com/1"


@pytest.mark.asyncio
async def test_news_cache_excludes_future_articles(_wire_store: CachedDataStore) -> None:
    """Articles published after ``as_of`` must not appear — no lookahead bias."""
    from backtest.providers import news_cache  # noqa: F401

    _wire_store.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL",
            url="https://example.com/future",
            headline="Future news",
            summary="",
            source="t",
            published_at=datetime(2023, 3, 20, tzinfo=UTC),  # after as_of
            sentiment=None,
        ),
    ])

    result = await news_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result == []


# ── stats_cache ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stats_cache_returns_stock_stats(_wire_store: CachedDataStore) -> None:
    """``stats_cache.fetch`` returns a ``StockStats`` instance from the cache."""
    from backtest.providers import stats_cache  # noqa: F401

    snapshot = StockStats(
        ticker="AAPL",
        history=[],
        market_cap=2_000_000_000_000,
        trailing_pe=28.5,
        forward_pe=25.0,
        beta=1.2,
        dividend_yield=0.006,
        fifty_day_average=155.0,
        two_hundred_day_average=148.0,
        last_price=170.0,
        sector="Technology",
        long_name="Apple Inc.",
    )
    _wire_store.write_market_meta("AAPL", snapshot, as_of_date=date(2023, 3, 9))

    result = await stats_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert isinstance(result, StockStats)
    assert result.ticker == "AAPL"
    assert result.sector == "Technology"


@pytest.mark.asyncio
async def test_stats_cache_returns_none_on_miss(_wire_store: CachedDataStore) -> None:
    """``stats_cache.fetch`` returns ``None`` when no data is cached."""
    from backtest.providers import stats_cache  # noqa: F401

    result = await stats_cache.fetch(
        "UNKNOWN",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result is None


# ── social_sentiment_cache ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_social_cache_returns_none(_wire_store: CachedDataStore) -> None:
    """Social sentiment is deliberately unavailable in v1 backtest — return None.

    Historical social-sentiment ingestion is a separate backlog item; the
    strategist already tolerates ``social=None`` evidence gracefully.
    """
    from backtest.providers import social_sentiment_cache  # noqa: F401

    result = await social_sentiment_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result is None


# ── insider_trades_cache ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insider_trades_cache_returns_pydantic_models(
    _wire_store: CachedDataStore,
) -> None:
    """``insider_trades_cache.fetch`` returns ``list[InsiderTrade]`` from cache."""
    from backtest.providers import insider_trades_cache  # noqa: F401

    trade = InsiderTrade(
        ticker="AAPL",
        insider_name="Tim Apple",
        insider_title="CEO",
        side="sell",
        shares=10_000,
        price_per_share=170.00,
        transaction_date=date(2023, 3, 1),
        filed_at=datetime(2023, 3, 3, tzinfo=UTC),
        form_type="4",
        transaction_code="S",
        is_10b5_1=False,
        footnote=None,
    )
    _wire_store.write_insider_trades("AAPL", [trade])

    result = await insider_trades_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], InsiderTrade)
    assert result[0].insider_name == "Tim Apple"


@pytest.mark.asyncio
async def test_insider_trades_cache_pit_filter(_wire_store: CachedDataStore) -> None:
    """Trades filed after ``as_of`` must not appear (lookahead filter on ``filed_at``)."""
    from backtest.providers import insider_trades_cache  # noqa: F401

    trade = InsiderTrade(
        ticker="AAPL",
        insider_name="Future Insider",
        insider_title="CFO",
        side="buy",
        shares=5_000,
        price_per_share=160.00,
        transaction_date=date(2023, 3, 5),
        filed_at=datetime(2023, 3, 20, tzinfo=UTC),  # filed after as_of
        form_type="4",
        transaction_code="P",
        is_10b5_1=False,
        footnote=None,
    )
    _wire_store.write_insider_trades("AAPL", [trade])

    result = await insider_trades_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result == []


# ── politician_trades_cache ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_politician_trades_cache_returns_pydantic_models(
    _wire_store: CachedDataStore,
) -> None:
    """``politician_trades_cache.fetch`` returns ``list[PoliticianTrade]``."""
    from backtest.providers import politician_trades_cache  # noqa: F401

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Sen. Test Person",
        chamber="senate",
        party="Independent",
        side="buy",
        transaction_date=date(2023, 3, 1),
        disclosure_date=date(2023, 3, 10),
        amount_min_usd=15_000,
        amount_max_usd=50_000,
    )
    _wire_store.write_politician_trades("AAPL", [trade])

    result = await politician_trades_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], PoliticianTrade)
    assert result[0].politician == "Sen. Test Person"


# ── notable_holders_cache ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notable_holders_cache_returns_pydantic_models(
    _wire_store: CachedDataStore,
) -> None:
    """``notable_holders_cache.fetch`` returns ``list[NotableHolder]``."""
    from backtest.providers import notable_holders_cache  # noqa: F401

    holder = NotableHolder(
        ticker="AAPL",
        accession_no="0001234-23-001234",
        holder="Vanguard Group",
        form_type="13G",
        intent="passive",
        is_amendment=False,
        filed_at=datetime(2023, 2, 15, tzinfo=UTC),
        url="https://sec.gov/filing/1",
    )
    _wire_store.write_notable_holders("AAPL", [holder])

    result = await notable_holders_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], NotableHolder)
    assert result[0].holder == "Vanguard Group"


# ── filings_cache ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filings_cache_returns_pydantic_models(
    _wire_store: CachedDataStore,
) -> None:
    """``filings_cache.fetch`` returns ``list[Filing]``."""
    from backtest.providers import filings_cache  # noqa: F401

    filing = Filing(
        ticker="AAPL",
        accession_no="0001234-23-005678",
        form_type="10-Q",
        filed_at=datetime(2023, 2, 1, tzinfo=UTC),
        title="Quarterly Report",
        url="https://sec.gov/filing/2",
        risk_factors_excerpt=None,
        mda_excerpt=None,
    )
    _wire_store.write_filings("AAPL", [filing])

    result = await filings_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert len(result) == 1
    assert isinstance(result[0], Filing)
    assert result[0].form_type == "10-Q"


@pytest.mark.asyncio
async def test_filings_cache_pit_filter(_wire_store: CachedDataStore) -> None:
    """Filings filed after ``as_of`` must not appear."""
    from backtest.providers import filings_cache  # noqa: F401

    filing = Filing(
        ticker="AAPL",
        accession_no="0001234-23-009999",
        form_type="8-K",
        filed_at=datetime(2023, 3, 20, tzinfo=UTC),  # after as_of
        title="Current Report",
        url="https://sec.gov/filing/3",
        risk_factors_excerpt=None,
        mda_excerpt=None,
    )
    _wire_store.write_filings("AAPL", [filing])

    result = await filings_cache.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert result == []
