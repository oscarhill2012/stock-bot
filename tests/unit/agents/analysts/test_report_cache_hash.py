"""Unit tests for the report-cache hash primitives.

Adapted from the plan spec to match the actual model field names:

- ``NewsArticle`` uses ``headline``, not ``title``.
- ``Filing`` requires ``url``, ``filed_at`` is ``datetime`` (not ``date``).
- ``InsiderTrade`` requires ``ticker``, ``filed_at``, and ``form_type``.
- ``InsiderTrade.price_per_share`` is optional (may be ``None``).
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from agents.analysts.report_cache import (
    fundamental_hash_inputs,
    news_hash_inputs,
)
from data.models import (
    CompanyRatios,
    Filing,
    Form4Bundle,
    InsiderTrade,
    NewsArticle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _article(url: str, published: str) -> NewsArticle:
    """Build a minimal NewsArticle for testing.

    Parameters
    ----------
    url:
        Article URL — forms the primary cache key along with ``published``.
    published:
        ISO-8601 string for ``published_at``.

    Returns
    -------
    NewsArticle
        Minimal but fully-valid instance.
    """
    return NewsArticle(
        url=url,
        headline="t",   # actual field is ``headline``, not ``title``
        summary="s",
        published_at=datetime.fromisoformat(published),
        source="src",
        ticker="AAPL",
    )


def _filing(accession_no: str, form_type: str = "10-Q") -> Filing:
    """Build a minimal Filing for testing.

    ``Filing.url`` is required; ``filed_at`` must be a ``datetime``.

    Parameters
    ----------
    accession_no:
        Unique accession number used as the filing's cache key.
    form_type:
        SEC form type string.

    Returns
    -------
    Filing
        Minimal but fully-valid instance.
    """
    return Filing(
        ticker="AAPL",
        form_type=form_type,
        filed_at=datetime(2026, 5, 1, tzinfo=UTC),
        accession_no=accession_no,
        url="https://sec.gov/fake",
        mda_excerpt="m",
        risk_factors_excerpt="r",
    )


def _insider_trade(
    insider_name: str,
    transaction_date: date,
    shares: float = 1000.0,
    price_per_share: float = 210.0,
) -> InsiderTrade:
    """Build a minimal InsiderTrade for testing.

    ``InsiderTrade`` requires ``ticker``, ``filed_at``, and ``form_type``
    in addition to the fields used by the hash function.

    Parameters
    ----------
    insider_name:
        Insider's full name.
    transaction_date:
        Date of the trade — used as part of the cache key.
    shares:
        Number of shares transacted.
    price_per_share:
        Price per share (used in hash rounded to 2 dp).

    Returns
    -------
    InsiderTrade
        Minimal but fully-valid frozen instance.
    """
    return InsiderTrade(
        ticker="AAPL",
        insider_name=insider_name,
        insider_title="CFO",
        side="sell",
        shares=shares,
        price_per_share=price_per_share,
        transaction_date=transaction_date,
        filed_at=datetime(2026, 5, 12, tzinfo=UTC),
        form_type="4",
        is_10b5_1=False,
    )


# ---------------------------------------------------------------------------
# News hash tests
# ---------------------------------------------------------------------------

def test_news_hash_stable_under_reordering() -> None:
    """The hash must be insensitive to article ordering."""
    a = _article("https://a", "2026-05-13T10:00:00")
    b = _article("https://b", "2026-05-13T11:00:00")

    assert news_hash_inputs([a, b]) == news_hash_inputs([b, a])


def test_news_hash_changes_on_new_article() -> None:
    """Adding a single article must invalidate the cache."""
    a = _article("https://a", "2026-05-13T10:00:00")
    b = _article("https://b", "2026-05-13T11:00:00")
    c = _article("https://c", "2026-05-13T12:00:00")

    assert news_hash_inputs([a, b]) != news_hash_inputs([a, b, c])


# ---------------------------------------------------------------------------
# Fundamental hash tests
# ---------------------------------------------------------------------------

def test_fundamental_hash_stable_under_float_jitter() -> None:
    """Float jitter at the 5th decimal place must NOT bust the cache.

    The hash function rounds ratios floats to 4 dp, so differences at the
    5th decimal place (rounding artefacts) must produce identical digests.
    """
    r1 = CompanyRatios(ticker="AAPL", trailing_pe=36.23879)
    r2 = CompanyRatios(ticker="AAPL", trailing_pe=36.23880)
    bundle = Form4Bundle(trades=[], derivatives=[])

    assert fundamental_hash_inputs(r1, [], bundle) == fundamental_hash_inputs(r2, [], bundle)


def test_fundamental_hash_changes_on_new_filing() -> None:
    """Adding a Filing must invalidate the cache."""
    r = CompanyRatios(ticker="AAPL", trailing_pe=36.0)
    bundle = Form4Bundle(trades=[], derivatives=[])
    f1 = _filing("A1", "10-Q")
    f2 = _filing("A2", "8-K")

    assert fundamental_hash_inputs(r, [f1], bundle) != fundamental_hash_inputs(r, [f1, f2], bundle)


def test_fundamental_hash_changes_on_new_insider_trade() -> None:
    """Adding a Form 4 trade must invalidate the cache."""
    r = CompanyRatios(ticker="AAPL", trailing_pe=36.0)
    t = _insider_trade("J Doe", date(2026, 5, 12))
    b1 = Form4Bundle(trades=[],  derivatives=[])
    b2 = Form4Bundle(trades=[t], derivatives=[])

    assert fundamental_hash_inputs(r, [], b1) != fundamental_hash_inputs(r, [], b2)


def test_fundamental_hash_ignores_daily_price_moves() -> None:
    """Daily price-driven ratio moves must NOT bust the fundamental cache.

    ``last_price``, ``market_cap``, the two moving averages, the price-based
    ``trailing_pe`` / ``forward_pe`` / ``peg`` / ``dividend_yield`` and the
    52-week extremes all re-quote every trading day even when no new filing has
    arrived.  Including them in the cache key forced a full filing-LLM re-run
    every morning for an identical verdict.  They stay in the prompt (the
    analyst still sees fresh prices when it actually runs) but must be excluded
    from the key so unchanged filings reuse the cached report.
    """
    bundle = Form4Bundle(trades=[], derivatives=[])

    # Identical fundamentals; only price-derived fields differ between the two
    # trading days (the exact pattern observed for AAPL Feb 10 → Feb 11).
    day1 = CompanyRatios(
        ticker="AAPL", roe=0.50, revenue_growth_yoy=0.07,
        last_price=273.0, market_cap=4_014_000_000_000.0,
        trailing_pe=113.4, forward_pe=30.1, peg=2.5, dividend_yield=0.0040,
        fifty_day_average=260.0, two_hundred_day_average=240.0,
        fifty_two_week_high=290.0, fifty_two_week_low=210.0,
    )
    day2 = CompanyRatios(
        ticker="AAPL", roe=0.50, revenue_growth_yoy=0.07,
        last_price=280.0, market_cap=4_120_000_000_000.0,
        trailing_pe=116.0, forward_pe=31.0, peg=2.6, dividend_yield=0.0039,
        fifty_day_average=262.0, two_hundred_day_average=241.0,
        fifty_two_week_high=290.0, fifty_two_week_low=210.0,
    )

    assert fundamental_hash_inputs(day1, [], bundle) == fundamental_hash_inputs(day2, [], bundle)


def test_fundamental_hash_ignores_as_of_retrieval_date() -> None:
    """The PIT retrieval date (``as_of``) is metadata, not signal.

    It advances every tick, so including it in the key would bust the cache
    daily regardless of the price fields.
    """
    bundle = Form4Bundle(trades=[], derivatives=[])

    r1 = CompanyRatios(ticker="AAPL", roe=0.50, as_of=date(2026, 2, 10))
    r2 = CompanyRatios(ticker="AAPL", roe=0.50, as_of=date(2026, 2, 11))

    assert fundamental_hash_inputs(r1, [], bundle) == fundamental_hash_inputs(r2, [], bundle)


def test_fundamental_hash_changes_on_fundamental_field() -> None:
    """A genuine fundamental change MUST still bust the cache.

    Guards against over-exclusion: narrowing the key to drop price noise must
    not accidentally drop true quarterly fundamentals like return on equity.
    """
    bundle = Form4Bundle(trades=[], derivatives=[])

    r1 = CompanyRatios(ticker="AAPL", roe=0.50, revenue_growth_yoy=0.07)
    r2 = CompanyRatios(ticker="AAPL", roe=0.55, revenue_growth_yoy=0.07)

    assert fundamental_hash_inputs(r1, [], bundle) != fundamental_hash_inputs(r2, [], bundle)
