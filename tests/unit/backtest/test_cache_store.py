"""Tests for the point-in-time-filtered cache store.

The point-in-time property is the most important correctness rule in the
whole harness: reads must NEVER return a row whose canonical timestamp is
after the supplied ``as_of``.  Lookahead bias would silently invalidate
every backtest.

Critical rule: PIT filters use *filing / publication* timestamps (``filed_at``,
``published_at``), never *transaction* dates.  The insider-trade tests below
assert this explicitly — a trade filed the day after ``as_of`` must not appear
even when its ``transaction_date`` precedes ``as_of``.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import (
    CompanyRatios,
    Filing,
    InsiderTrade,
    NewsArticle,
    NotableHolder,
    OHLCBar,
    PoliticianTrade,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    """Fresh empty cache store rooted in a temp dir."""
    return CachedDataStore(tmp_path / "store.sqlite")


# ── helpers ───────────────────────────────────────────────────────────────────

def _dt(year: int, month: int, day: int, hour: int = 0) -> datetime:
    """Convenience: tz-aware datetime at midnight UTC."""
    return datetime(year, month, day, hour, tzinfo=UTC)


# ── news ──────────────────────────────────────────────────────────────────────

def test_news_read_excludes_future_articles(store: CachedDataStore) -> None:
    """Articles published after ``as_of`` must not be returned."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/1", headline="Past",
            summary="", source="t", published_at=_dt(2023, 3, 8),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/2", headline="Future",
            summary="", source="t", published_at=_dt(2023, 3, 20),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news("AAPL", as_of=_dt(2023, 3, 15), lookback_days=30)

    assert [a.headline for a in result] == ["Past"]


def test_news_read_respects_lookback_lower_bound(store: CachedDataStore) -> None:
    """Articles older than ``lookback_days`` before ``as_of`` are excluded."""
    articles = [
        NewsArticle(
            ticker="AAPL", url="https://x/old", headline="Too Old",
            summary="", source="t", published_at=_dt(2023, 1, 1),
        ),
        NewsArticle(
            ticker="AAPL", url="https://x/recent", headline="Recent",
            summary="", source="t", published_at=_dt(2023, 3, 10),
        ),
    ]
    store.write_news("AAPL", articles)

    result = store.read_news("AAPL", as_of=_dt(2023, 3, 15), lookback_days=30)

    assert [a.headline for a in result] == ["Recent"]


def test_write_is_idempotent_on_primary_key(store: CachedDataStore) -> None:
    """Re-writing the same news article is a no-op, not a duplicate row."""
    article = NewsArticle(
        ticker="AAPL", url="https://x/dup", headline="H",
        summary="", source="t", published_at=_dt(2023, 3, 8),
    )
    store.write_news("AAPL", [article])
    store.write_news("AAPL", [article])

    result = store.read_news("AAPL", as_of=_dt(2023, 3, 15), lookback_days=30)
    assert len(result) == 1


# ── OHLCV ─────────────────────────────────────────────────────────────────────

def test_ohlcv_read_returns_inclusive_range(store: CachedDataStore) -> None:
    """``read_ohlcv(start, end)`` returns bars with date in ``[start, end]``."""
    # OHLCBar uses ``timestamp: datetime`` — midnight UTC for each day.
    bars = [
        OHLCBar(
            timestamp=_dt(2023, 3, d),
            open=1.0, high=2.0, low=0.5, close=1.5, volume=100.0,
        )
        for d in (6, 7, 8, 9, 10)
    ]
    store.write_ohlcv("AAPL", bars)

    result = store.read_ohlcv("AAPL", date(2023, 3, 7), date(2023, 3, 9))

    assert len(result) == 3
    assert [b.timestamp.date() for b in result] == [
        date(2023, 3, 7), date(2023, 3, 8), date(2023, 3, 9),
    ]


# ── company ratios ────────────────────────────────────────────────────────────

def test_company_ratios_pit_filter(store: CachedDataStore) -> None:
    """``read_company_ratios`` returns the latest snapshot ≤ as_of.date()."""
    early = CompanyRatios(ticker="AAPL", market_cap=1_000.0, trailing_pe=20.0)
    late  = CompanyRatios(ticker="AAPL", market_cap=2_000.0, trailing_pe=25.0)

    store.write_company_ratios("AAPL", early, date(2023, 3, 1))
    store.write_company_ratios("AAPL", late,  date(2023, 3, 15))

    # as_of before the late snapshot → should see early snapshot only.
    result = store.read_company_ratios("AAPL", as_of=_dt(2023, 3, 10))
    assert result is not None
    assert result.market_cap == 1_000.0

    # as_of on or after the late snapshot → should see late snapshot.
    result_late = store.read_company_ratios("AAPL", as_of=_dt(2023, 3, 15))
    assert result_late is not None
    assert result_late.market_cap == 2_000.0


def test_company_ratios_returns_none_when_no_data(store: CachedDataStore) -> None:
    """Returns ``None`` when there is no snapshot for the ticker at all."""
    result = store.read_company_ratios("AAPL", as_of=_dt(2023, 3, 10))
    assert result is None


# ── insider trades — PIT filter MUST use filed_at, not transaction_date ───────

def test_insider_trades_pit_uses_filed_at_not_transaction_date(
    store: CachedDataStore,
) -> None:
    """A trade whose transaction_date is before as_of but filed_at is after
    must NOT be returned — filtering on transaction_date would cause
    lookahead bias (Form 4 must be filed within 2 business days but the
    transaction can predate the filing).
    """
    # Transaction happened before as_of, but filing arrived after as_of.
    trade_leaked = InsiderTrade(
        ticker="AAPL",
        insider_name="CEO",
        insider_title="Chief Executive Officer",
        side="sell",
        shares=1_000.0,
        price_per_share=150.0,
        transaction_date=date(2023, 3, 10),   # before as_of
        filed_at=_dt(2023, 3, 20),             # after as_of → must be excluded
        form_type="4",
    )
    # This trade was both transacted and filed before as_of → must appear.
    trade_visible = InsiderTrade(
        ticker="AAPL",
        insider_name="CFO",
        insider_title="Chief Financial Officer",
        side="buy",
        shares=500.0,
        price_per_share=140.0,
        transaction_date=date(2023, 3, 5),
        filed_at=_dt(2023, 3, 7),             # before as_of → must appear
        form_type="4",
    )

    store.write_insider_trades("AAPL", [trade_leaked, trade_visible])

    result = store.read_insider_trades(
        "AAPL", as_of=_dt(2023, 3, 15), lookback_days=90,
    )

    names = [t.insider_name for t in result]
    assert "CFO" in names,  "visible trade (filed before as_of) must appear"
    assert "CEO" not in names, "leaked trade (filed after as_of) must not appear"


def test_insider_trades_extra_fields_round_trip(store: CachedDataStore) -> None:
    """transaction_code, is_10b5_1, and footnote survive a write/read cycle."""
    trade = InsiderTrade(
        ticker="AAPL",
        insider_name="Director",
        insider_title=None,
        side="sell",
        shares=200.0,
        price_per_share=155.0,
        transaction_date=date(2023, 3, 5),
        filed_at=_dt(2023, 3, 6),
        form_type="4",
        transaction_code="S",
        is_10b5_1=True,
        footnote="Sold per 10b5-1 plan adopted 2022-12-01.",
    )

    store.write_insider_trades("AAPL", [trade])
    result = store.read_insider_trades(
        "AAPL", as_of=_dt(2023, 3, 15), lookback_days=90,
    )

    assert len(result) == 1
    rt = result[0]
    assert rt.transaction_code == "S"
    assert rt.is_10b5_1 is True
    assert rt.footnote == "Sold per 10b5-1 plan adopted 2022-12-01."


# ── filings ───────────────────────────────────────────────────────────────────

def test_filings_pit_uses_filed_at(store: CachedDataStore) -> None:
    """Filings with ``filed_at > as_of`` must not be returned."""
    past_filing = Filing(
        ticker="AAPL", form_type="10-K", accession_no="0001-past",
        filed_at=_dt(2023, 1, 15), url="https://sec/past",
    )
    future_filing = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="0001-future",
        filed_at=_dt(2023, 4, 1), url="https://sec/future",
    )

    store.write_filings("AAPL", [past_filing, future_filing])

    result = store.read_filings(
        "AAPL", as_of=_dt(2023, 3, 15), lookback_days=365,
    )

    accessions = [f.accession_no for f in result]
    assert "0001-past" in accessions
    assert "0001-future" not in accessions


# ── notable holders ───────────────────────────────────────────────────────────

def test_notable_holders_pit_uses_filed_at(store: CachedDataStore) -> None:
    """Holders with ``filed_at > as_of`` must not be returned."""
    past_holder = NotableHolder(
        ticker="AAPL", holder="Berkshire", form_type="SC 13G",
        accession_no="berk-001", filed_at=_dt(2023, 2, 1), url=None,
    )
    future_holder = NotableHolder(
        ticker="AAPL", holder="Vanguard", form_type="SC 13G",
        accession_no="vang-001", filed_at=_dt(2023, 4, 1), url=None,
    )

    store.write_notable_holders("AAPL", [past_holder, future_holder])

    result = store.read_notable_holders(
        "AAPL", as_of=_dt(2023, 3, 15), lookback_days=365,
    )

    holders_found = [h.holder for h in result]
    assert "Berkshire" in holders_found
    assert "Vanguard" not in holders_found


# ── politician trades ─────────────────────────────────────────────────────────

def test_politician_trades_pit_uses_disclosure_date(store: CachedDataStore) -> None:
    """Politician trades are filtered by COALESCE(disclosure_date, transaction_date).

    A trade transacted before as_of but disclosed after must not appear —
    the STOCK Act gives lawmakers up to 45 days to disclose.
    """
    # Transacted before as_of, disclosed after → must be excluded.
    late_disclosed = PoliticianTrade(
        ticker="MSFT", politician="Sen. Smith",
        chamber="Senate", party="D",
        side="buy",
        transaction_date=date(2023, 3, 1),    # before as_of
        disclosure_date=date(2023, 3, 20),    # after as_of → excluded
        amount_min_usd=15_000.0, amount_max_usd=50_000.0,
    )
    # Both transacted and disclosed before as_of → must appear.
    early_disclosed = PoliticianTrade(
        ticker="MSFT", politician="Rep. Jones",
        chamber="House", party="R",
        side="sell",
        transaction_date=date(2023, 2, 20),
        disclosure_date=date(2023, 3, 5),     # before as_of → visible
        amount_min_usd=1_000.0, amount_max_usd=15_000.0,
    )

    store.write_politician_trades("MSFT", [late_disclosed, early_disclosed])

    result = store.read_politician_trades(
        "MSFT", as_of=_dt(2023, 3, 15), lookback_days=90,
    )

    politicians = [t.politician for t in result]
    assert "Rep. Jones" in politicians,  "early-disclosed trade must appear"
    assert "Sen. Smith" not in politicians, "late-disclosed trade must not appear"
