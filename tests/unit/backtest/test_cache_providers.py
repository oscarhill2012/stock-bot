"""Round-trip equivalence tests for every cache provider.

Each cache provider must return the same Pydantic shape the live provider
returns.  These tests write known data into an in-memory SQLite store, then
call the provider's ``fetch`` function and assert the returned objects are the
expected Pydantic model instances with the expected field values.

Deviations from the plan's literal test code (Phase B renames):
- ``StockStats`` → ``CompanyRatios`` (Phase B retired ``StockStats``).
- ``stats_cache`` → ``company_ratios_cache`` + ``price_history_cache``
  (``stats`` domain was split into two domains in Phase B).
- ``read_market_meta`` → ``read_company_ratios`` (method rename in Phase B).
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers import _store_handle
from data.models import (
    CompanyRatios,
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
    PriceHistory,
)


# ── shared fixture ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _wire_store(tmp_path: Path):
    """Give every test a fresh in-temp-dir store; clear the singleton after.

    Yields the ``CachedDataStore`` so individual tests can write seed data
    via the store's write methods.
    """
    store = CachedDataStore(tmp_path / "store.sqlite")
    _store_handle.set_store(store)
    yield store
    _store_handle.clear_store()


# ── news ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_news_cache_returns_pydantic_articles(_wire_store: CachedDataStore) -> None:
    """``news_cache.fetch`` returns ``list[NewsArticle]`` filtered by ``as_of``."""
    from backtest.providers import news_cache  # noqa: PLC0415

    _wire_store.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL",
            url="https://x/1",
            headline="H",
            summary="",
            source="t",
            published_at=datetime(2023, 3, 10, tzinfo=UTC),
        ),
    ])

    result = await news_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert len(result) == 1
    assert isinstance(result[0], NewsArticle)


@pytest.mark.asyncio
async def test_news_cache_excludes_future_articles(_wire_store: CachedDataStore) -> None:
    """Articles published after ``as_of`` must not be returned (PIT filter)."""
    from backtest.providers import news_cache  # noqa: PLC0415

    _wire_store.write_news("AAPL", [
        NewsArticle(
            ticker="AAPL",
            url="https://x/future",
            headline="Future",
            summary="",
            source="t",
            published_at=datetime(2023, 3, 20, tzinfo=UTC),
        ),
    ])

    result = await news_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=30,
    )

    assert result == []


# ── social sentiment ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_social_cache_returns_empty_model(_wire_store: CachedDataStore) -> None:
    """Social sentiment cache returns an empty ``SocialSentiment`` in v1.

    Real ingestion is deferred to backlog B19.  Until then the provider
    returns a well-typed empty model rather than ``None``, satisfying the
    canonical ``single / SocialSentiment`` contract.
    """
    from data.models.sentiment import SocialSentiment  # noqa: PLC0415

    from backtest.providers import social_sentiment_cache  # noqa: PLC0415

    result = await social_sentiment_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert isinstance(result, SocialSentiment)
    assert result.ticker == "AAPL"
    assert result.snapshots == []
    assert result.aggregate_score == 0.0


# ── company ratios ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_company_ratios_cache_returns_pydantic_model(
    _wire_store: CachedDataStore,
) -> None:
    """``company_ratios_cache.fetch`` returns a ``CompanyRatios`` when a snapshot exists."""
    from backtest.providers import company_ratios_cache  # noqa: PLC0415

    snapshot = CompanyRatios(
        ticker="AAPL",
        long_name="Apple Inc.",
        sector="Technology",
        market_cap=2_800_000_000_000.0,
        last_price=175.0,
    )
    _wire_store.write_company_ratios("AAPL", snapshot, as_of_date=date(2023, 3, 10))

    result = await company_ratios_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC),
    )

    assert isinstance(result, CompanyRatios)
    assert result.long_name == "Apple Inc."


@pytest.mark.asyncio
async def test_company_ratios_cache_raises_when_empty(
    _wire_store: CachedDataStore,
) -> None:
    """Raises ``KeyError`` when no snapshot exists before ``as_of``.

    The canonical shape for company_ratios is ``single / CompanyRatios``; the
    cache must not return ``None`` because that would diverge from the live
    provider which always returns a ``CompanyRatios``.  Callers treat the
    ``KeyError`` as "no data available for this ticker at this date".
    """
    import pytest as _pytest  # noqa: PLC0415
    from backtest.providers import company_ratios_cache  # noqa: PLC0415

    with _pytest.raises(KeyError, match="no company_ratios snapshot"):
        await company_ratios_cache.fetch(
            "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC),
        )


# ── price history ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_price_history_cache_returns_pydantic_model(
    _wire_store: CachedDataStore,
) -> None:
    """``price_history_cache.fetch`` returns a ``PriceHistory`` with ``OHLCBar`` items."""
    from backtest.providers import price_history_cache  # noqa: PLC0415

    bar = OHLCBar(
        timestamp=datetime(2023, 3, 10, tzinfo=UTC),
        open=170.0,
        high=175.0,
        low=168.0,
        close=173.0,
        volume=50_000_000.0,
    )
    _wire_store.write_ohlcv("AAPL", [bar])

    result = await price_history_cache.fetch("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC))

    assert isinstance(result, PriceHistory)
    assert len(result.bars) == 1
    assert isinstance(result.bars[0], OHLCBar)
    assert result.bars[0].close == pytest.approx(173.0)


# ── insider trades ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_insider_trades_cache_returns_pydantic_list(
    _wire_store: CachedDataStore,
) -> None:
    """``insider_trades_cache.fetch`` returns ``list[InsiderTrade]``."""
    from backtest.providers import insider_trades_cache  # noqa: PLC0415

    trade = InsiderTrade(
        ticker="AAPL",
        insider_name="Tim Cook",
        side="sell",
        shares=10_000.0,
        transaction_date=date(2023, 3, 8),
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        form_type="4",
    )
    _wire_store.write_insider_trades("AAPL", [trade])

    result = await insider_trades_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=90,
    )

    # The cache provider wraps rows in a Form4Bundle to match the live
    # pipeline's expected shape (smart_money agent dispatch requires a bundle).
    from data.models.trades import Form4Bundle  # noqa: PLC0415
    assert isinstance(result, Form4Bundle), (
        f"Expected Form4Bundle, got {type(result).__name__}"
    )
    assert len(result.trades) == 1
    assert isinstance(result.trades[0], InsiderTrade)
    assert result.trades[0].insider_name == "Tim Cook"


# ── politician trades ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_politician_trades_cache_returns_pydantic_list(
    _wire_store: CachedDataStore,
) -> None:
    """``politician_trades_cache.fetch`` returns ``list[PoliticianTrade]``."""
    from backtest.providers import politician_trades_cache  # noqa: PLC0415

    trade = PoliticianTrade(
        ticker="AAPL",
        politician="Nancy Pelosi",
        side="buy",
        transaction_date=date(2023, 3, 5),
        disclosure_date=date(2023, 3, 10),
        amount_min_usd=15_000.0,
        amount_max_usd=50_000.0,
        chamber="House",
        party="Democrat",
    )
    _wire_store.write_politician_trades("AAPL", [trade])

    result = await politician_trades_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=90,
    )

    assert len(result) == 1
    assert isinstance(result[0], PoliticianTrade)
    assert result[0].politician == "Nancy Pelosi"


# ── notable holders ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notable_holders_cache_returns_pydantic_list(
    _wire_store: CachedDataStore,
) -> None:
    """``notable_holders_cache.fetch`` returns ``list[NotableHolder]``."""
    from backtest.providers import notable_holders_cache  # noqa: PLC0415

    holder = NotableHolder(
        ticker="AAPL",
        holder="Berkshire Hathaway",
        form_type="SC 13G",
        intent="passive",
        is_amendment=False,
        filed_at=datetime(2023, 2, 14, tzinfo=UTC),
        accession_no="0001193125-23-012345",
        url="https://www.sec.gov/Archives/edgar/data/12345/000119312523012345/0001193125-23-012345-index.htm",
    )
    _wire_store.write_notable_holders("AAPL", [holder])

    result = await notable_holders_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), lookback_days=180,
    )

    assert len(result) == 1
    assert isinstance(result[0], NotableHolder)
    assert result[0].holder == "Berkshire Hathaway"


# ── filings ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_filings_cache_returns_pydantic_list(
    _wire_store: CachedDataStore,
) -> None:
    """``filings_cache.fetch`` returns ``list[Filing]``."""
    from backtest.providers import filings_cache  # noqa: PLC0415

    filing = Filing(
        ticker="AAPL",
        form_type="10-K",
        filed_at=datetime(2023, 2, 2, tzinfo=UTC),
        accession_no="0000320193-23-000006",
        title="Annual Report",
        url="https://www.sec.gov/Archives/edgar/data/320193/000032019323000006/0000320193-23-000006-index.htm",
    )
    _wire_store.write_filings("AAPL", [filing])

    result = await filings_cache.fetch(
        "AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), staleness_days=90,
    )

    assert len(result) == 1
    assert isinstance(result[0], Filing)
    assert result[0].form_type == "10-K"


@pytest.mark.asyncio
async def test_filings_cache_applies_shared_selection_rule(
    _wire_store: CachedDataStore,
) -> None:
    """``filings_cache.fetch`` serves the shared analyst-visibility selection.

    The cache holds the raw backfill superset (superseded periodic filings,
    stale 8-Ks); the provider must apply ``select_current_filings`` per tick
    so replay serves exactly what live would — latest 10-K, latest 10-Q,
    and only the 8-Ks inside the staleness horizon.
    """
    from backtest.providers import filings_cache  # noqa: PLC0415

    as_of = datetime(2023, 9, 15, tzinfo=UTC)

    def _f(form: str, filed: datetime, acc: str) -> Filing:
        """Build a minimal cached ``Filing`` row for the selection test."""
        return Filing(ticker="AAPL", form_type=form, filed_at=filed, accession_no=acc)

    _wire_store.write_filings("AAPL", [
        _f("10-K", datetime(2021, 11, 5, tzinfo=UTC), "K-superseded"),
        _f("10-K", datetime(2022, 11, 4, tzinfo=UTC), "K-current"),     # ~10 months old — still the anchor
        _f("10-Q", datetime(2023, 5, 5,  tzinfo=UTC), "Q-superseded"),
        _f("10-Q", datetime(2023, 8, 4,  tzinfo=UTC), "Q-current"),
        _f("8-K",  datetime(2023, 9, 1,  tzinfo=UTC), "E-fresh"),       # inside 90-day horizon
        _f("8-K",  datetime(2023, 4, 1,  tzinfo=UTC), "E-stale"),       # outside 90-day horizon
    ])

    result = await filings_cache.fetch("AAPL", as_of=as_of, staleness_days=90)

    assert {f.accession_no for f in result} == {"K-current", "Q-current", "E-fresh"}
