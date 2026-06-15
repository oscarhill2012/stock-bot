"""Unit tests for ``_build_ticker_news_context``, ``_parse_published``,
``_score_article_specificity``, and ``_rerank_articles``
in ``agents.analysts.news.fetch``.

Covers:
  (a) Rendered context contains the ``As of:`` anchor line.
  (b) A known ``published_at`` vs a known ``as_of`` yields the correct
      ``Nd ago`` age label.
  (c) Missing / ``MISSING_TIMESTAMP`` / unparseable published date renders
      ``age unknown`` without raising an exception.
  (d) Negative ages (future-dated dirty data) are clamped to ``0d ago``.
  (e) Timezone-aware ``published_at`` strings are handled without
      a tz-subtraction ``TypeError``.
  (f) Specificity scoring — headline match scores 2, summary-only match
      scores 1, no match scores 0.
  (g) Re-ranking — specific articles appear before generics in output.
  (h) Generic cap enforced; total cap respected; thin-ticker fallback.
  (i) Case-insensitive name matching and first-word match.
  (j) Specifics exceeding total cap → zero generics included.
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from agents.analysts.news.fetch import (
    _build_ticker_news_context,
    _parse_published,
    _rerank_articles,
    _score_article_specificity,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_article(
    title: str = "Headline",
    summary: str = "Short summary.",
    published_at: str | None = "2025-10-05",
) -> dict:
    """Return a minimal article dict as produced by the provider serialiser."""
    return {"title": title, "summary": summary, "published_at": published_at}


def _make_fake_caps(
    max_articles: int = 10,
    max_generic: int = 5,
    max_summary_chars: int = 200,
) -> MagicMock:
    """Return a MagicMock that mimics a ``NewsCaps`` object.

    Parameters
    ----------
    max_articles:
        Value for ``max_articles_per_ticker``.
    max_generic:
        Value for ``max_generic_articles_per_ticker``.
    max_summary_chars:
        Value for ``max_summary_chars``.

    Returns
    -------
    MagicMock
        A mock with the three fields set.
    """
    caps = MagicMock()
    caps.max_articles_per_ticker         = max_articles
    caps.max_generic_articles_per_ticker = max_generic
    caps.max_summary_chars               = max_summary_chars
    return caps


def _build(
    articles: list,
    *,
    as_of: datetime,
    ticker: str = "AAPL",
    company_name: str | None = None,
    max_articles: int = 10,
    max_generic: int = 5,
) -> str:
    """Call ``_build_ticker_news_context`` with config caps patched to sane values.

    Uses a ``MagicMock`` instead of constructing a real ``NewsCaps`` object
    so the test is insulated from changes to ``NewsCaps`` field requirements.
    The mock exposes the three fields read by ``_build_ticker_news_context``:
    ``max_articles_per_ticker``, ``max_generic_articles_per_ticker``, and
    ``max_summary_chars``.

    Parameters
    ----------
    articles:
        Article list to pass through.
    as_of:
        Reference datetime for age computation.
    ticker:
        Ticker symbol; defaults to ``"AAPL"``.
    company_name:
        Optional company name for specificity matching.
    max_articles:
        Override for ``max_articles_per_ticker`` on the fake caps.
    max_generic:
        Override for ``max_generic_articles_per_ticker`` on the fake caps.

    Returns
    -------
    str
        The rendered context block.
    """
    fake_caps = _make_fake_caps(max_articles=max_articles, max_generic=max_generic)

    with patch("agents.analysts.news.fetch._caps", return_value=fake_caps):
        return _build_ticker_news_context(
            ticker, articles, as_of=as_of, company_name=company_name,
        )


# ---------------------------------------------------------------------------
# (a) As-of anchor
# ---------------------------------------------------------------------------

def test_as_of_anchor_present():
    """The ``As of:`` reference date must appear immediately after the ticker header."""
    as_of = datetime(2025, 10, 10, 16, 0)
    result = _build([_make_article()], as_of=as_of)

    assert "As of: 2025-10-10" in result, (
        "Expected 'As of: 2025-10-10' anchor in rendered block; got:\n" + result
    )


def test_as_of_anchor_present_with_no_articles():
    """The anchor must be present even when there are no articles."""
    as_of = datetime(2026, 3, 1, 9, 0)
    result = _build([], as_of=as_of)

    assert "As of: 2026-03-01" in result
    assert "(no news available)" in result


# ---------------------------------------------------------------------------
# (b) Correct age computation
# ---------------------------------------------------------------------------

def test_age_correct_for_known_dates():
    """A 5-day-old article must be labelled ``5d ago``."""
    as_of       = datetime(2025, 10, 10, 16, 0)   # 10 October 2025
    published   = "2025-10-05"                     # 5 October 2025 → 5 days prior
    result      = _build([_make_article(published_at=published)], as_of=as_of)

    assert "5d ago" in result, (
        f"Expected '5d ago' in context block; got:\n{result}"
    )
    # The ISO date prefix should also appear.
    assert "2025-10-05" in result


def test_age_zero_for_same_day_article():
    """An article published on the same calendar day as ``as_of`` is ``0d ago``."""
    as_of     = datetime(2025, 10, 10, 16, 0)
    published = "2025-10-10T08:00:00"
    result    = _build([_make_article(published_at=published)], as_of=as_of)

    assert "0d ago" in result


def test_age_with_timezone_aware_published_string():
    """A ``published_at`` with a ``+00:00`` suffix must parse without TypeError."""
    as_of     = datetime(2025, 10, 10, 16, 0)   # naive
    published = "2025-10-08T12:00:00+00:00"     # tz-aware — 2 days prior
    result    = _build([_make_article(published_at=published)], as_of=as_of)

    assert "2d ago" in result


def test_age_with_z_suffix_in_published_string():
    """The ``Z`` Zulu suffix (as produced by many APIs) is correctly normalised."""
    as_of     = datetime(2025, 10, 10, 16, 0)
    published = "2025-10-07T00:00:00Z"           # 3 days prior
    result    = _build([_make_article(published_at=published)], as_of=as_of)

    assert "3d ago" in result


def test_age_with_timezone_aware_as_of():
    """A tz-aware ``as_of`` is coerced to naive UTC before age arithmetic."""
    as_of     = datetime(2025, 10, 10, 16, 0, tzinfo=UTC)
    published = "2025-10-09"   # 1 day prior
    result    = _build([_make_article(published_at=published)], as_of=as_of)

    assert "1d ago" in result


# ---------------------------------------------------------------------------
# (c) Missing / sentinel / unparseable published dates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_value", [
    None,
    "",
    "MISSING_TIMESTAMP",
    "not-a-date",
    "???",
])
def test_missing_or_unparseable_published_renders_age_unknown(bad_value):
    """Any absent / sentinel / garbage ``published_at`` must render ``age unknown``."""
    as_of  = datetime(2025, 10, 10)
    result = _build([_make_article(published_at=bad_value)], as_of=as_of)

    # Must not crash, and must say "age unknown".
    assert "age unknown" in result, (
        f"Expected 'age unknown' for published_at={bad_value!r}; got:\n{result}"
    )


def test_missing_published_does_not_raise():
    """Calling the builder with a missing published date must not raise any exception."""
    as_of = datetime(2025, 10, 10)
    _build([_make_article(published_at=None)], as_of=as_of)   # must not raise


# ---------------------------------------------------------------------------
# (d) Negative age clamping
# ---------------------------------------------------------------------------

def test_future_dated_article_clamped_to_zero():
    """An article dated *after* ``as_of`` (dirty data) must render as ``0d ago``."""
    as_of       = datetime(2025, 10, 10)
    future_date = "2025-10-15"   # 5 days in the future relative to as_of
    result      = _build([_make_article(published_at=future_date)], as_of=as_of)

    assert "0d ago" in result, (
        f"Expected negative age to be clamped to '0d ago'; got:\n{result}"
    )
    # Explicitly confirm a negative label is never shown.
    import re
    assert not re.search(r"-\d+d ago", result), "Negative age label found in context block"


# ---------------------------------------------------------------------------
# _parse_published unit tests
# ---------------------------------------------------------------------------

def test_parse_published_none_returns_none():
    """``None`` input must return ``None``."""
    assert _parse_published(None) is None


def test_parse_published_empty_string_returns_none():
    """Empty string must return ``None``."""
    assert _parse_published("") is None


def test_parse_published_sentinel_returns_none():
    """The ``MISSING_TIMESTAMP`` sentinel must return ``None``."""
    assert _parse_published("MISSING_TIMESTAMP") is None


def test_parse_published_iso_date_only():
    """A bare ``YYYY-MM-DD`` string produces a naive datetime at midnight."""
    result = _parse_published("2025-06-15")
    assert result == datetime(2025, 6, 15, 0, 0, 0)
    assert result.tzinfo is None


def test_parse_published_strips_timezone():
    """A tz-aware ISO string is returned as naive UTC."""
    result = _parse_published("2025-06-15T12:00:00+05:30")
    assert result is not None
    assert result.tzinfo is None
    # 12:00 +05:30 = 06:30 UTC
    assert result.hour == 6
    assert result.minute == 30


def test_parse_published_datetime_aware_strips_tz():
    """A tz-aware ``datetime`` object is coerced to naive UTC."""
    aware = datetime(2025, 6, 15, 10, 0, tzinfo=UTC)
    result = _parse_published(aware)
    assert result == datetime(2025, 6, 15, 10, 0)
    assert result.tzinfo is None


def test_parse_published_datetime_naive_passthrough():
    """A naive ``datetime`` is returned as-is."""
    naive = datetime(2025, 6, 15, 10, 0)
    result = _parse_published(naive)
    assert result == naive
    assert result.tzinfo is None


# ---------------------------------------------------------------------------
# (f) _score_article_specificity — scoring rules
# ---------------------------------------------------------------------------

def test_score_headline_match_returns_2():
    """An article whose headline contains the ticker symbol scores 2."""
    article = _make_article(title="AAPL hits all-time high", summary="Generic market news.")
    assert _score_article_specificity(article, "AAPL", None) == 2


def test_score_headline_company_name_match_returns_2():
    """An article whose headline contains the company name (not the symbol) scores 2."""
    article = _make_article(title="Apple announces new MacBook lineup", summary="No ticker here.")
    assert _score_article_specificity(article, "AAPL", "Apple") == 2


def test_score_summary_only_match_returns_1():
    """An article whose summary (but not headline) mentions the company scores 1."""
    article = _make_article(
        title="Tech stocks rally on Fed pivot hopes",
        summary="Apple's earnings next week are the key catalyst.",
    )
    assert _score_article_specificity(article, "AAPL", "Apple") == 1


def test_score_no_match_returns_0():
    """An article that mentions neither ticker nor company name scores 0."""
    article = _make_article(
        title="Risk Is Back On — Dow Hits 50,000",
        summary="Broad market sentiment turned bullish as macro fears eased.",
    )
    assert _score_article_specificity(article, "AAPL", "Apple") == 0


def test_score_case_insensitive_headline_match():
    """Headline matching is case-insensitive — 'apple' matches name 'Apple'."""
    article = _make_article(
        title="apple's iphone sales disappoint analysts",
        summary="Some other content.",
    )
    assert _score_article_specificity(article, "AAPL", "Apple") == 2


def test_score_first_word_name_match():
    """The first word of a multi-word company name also triggers a headline match.

    'Lockheed' in a headline should score 2 even though the company name is
    'Lockheed Martin' — financial journalism routinely drops the second word.
    """
    article = _make_article(
        title="Lockheed wins $5bn Pentagon contract",
        summary="No other company names here.",
    )
    assert _score_article_specificity(article, "LMT", "Lockheed Martin") == 2


def test_score_first_word_only_in_summary():
    """First-word match in summary (but not headline) scores 1, not 2."""
    article = _make_article(
        title="Defence stocks rally on budget news",
        summary="Lockheed is expected to be the biggest beneficiary.",
    )
    assert _score_article_specificity(article, "LMT", "Lockheed Martin") == 1


def test_score_headline_beats_summary():
    """Headline match always wins; summary-only match cannot override to score 2."""
    # Verify the score is exactly 2 when both headline and summary match.
    article = _make_article(
        title="Apple beats Q3 revenue estimates",
        summary="Apple reported strong iPhone sales this quarter.",
    )
    assert _score_article_specificity(article, "AAPL", "Apple") == 2


# ---------------------------------------------------------------------------
# (g) _rerank_articles — ordering
# ---------------------------------------------------------------------------

def test_rerank_specific_before_generic():
    """Specific articles (score ≥ 1) must appear before generic (score 0) ones."""
    generic_article  = _make_article(title="Risk Is Back On", published_at="2025-10-10")
    specific_article = _make_article(title="Apple beats earnings", published_at="2025-10-09")

    # Feed them in generic-first order to confirm ordering is applied.
    result = _rerank_articles(
        [generic_article, specific_article],
        ticker="AAPL",
        company_name="Apple",
        max_total=10,
        max_generic=5,
    )

    # The specific article should be first despite arriving second.
    assert result[0] is specific_article
    assert result[1] is generic_article


def test_rerank_headline_match_before_summary_match():
    """A headline match (score 2) must sort before a summary-only match (score 1)."""
    summary_match = _make_article(
        title="Tech rally continues",
        summary="Apple's strong results buoyed the sector.",
        published_at="2025-10-10",   # more recent
    )
    headline_match = _make_article(
        title="Apple smashes revenue record",
        summary="Strong iPhone demand.",
        published_at="2025-10-09",   # older
    )

    result = _rerank_articles(
        [summary_match, headline_match],
        ticker="AAPL",
        company_name="Apple",
        max_total=10,
        max_generic=5,
    )

    # Headline match (score 2) must come first even though it is older.
    assert result[0] is headline_match
    assert result[1] is summary_match


# ---------------------------------------------------------------------------
# (h) Generic cap and total cap enforcement
# ---------------------------------------------------------------------------

def test_generic_cap_enforced():
    """When more generics exist than the cap allows, only the cap number are kept."""
    generics = [
        _make_article(
            title=f"Generic story {i}",
            summary="Market roundup.",
            published_at=f"2025-10-{i:02d}",
        )
        for i in range(1, 11)   # 10 generic articles
    ]

    result = _rerank_articles(
        generics,
        ticker="AAPL",
        company_name="Apple",
        max_total=25,   # total cap is generous
        max_generic=5,  # generic cap should trim to 5
    )

    assert len(result) == 5, (
        f"Expected exactly 5 generic articles; got {len(result)}"
    )


def test_total_cap_respected_with_mix():
    """The total cap must not be exceeded even with a mix of specific + generic."""
    specific = [
        _make_article(title=f"Apple news {i}", published_at="2025-10-05")
        for i in range(8)
    ]
    generics = [
        _make_article(title=f"Macro roundup {i}", published_at="2025-10-05")
        for i in range(10)
    ]

    result = _rerank_articles(
        specific + generics,
        ticker="AAPL",
        company_name="Apple",
        max_total=10,   # hard cap
        max_generic=5,
    )

    assert len(result) <= 10, (
        f"Total cap of 10 exceeded; got {len(result)} articles"
    )


def test_thin_ticker_returns_up_to_generic_cap():
    """A ticker with zero specific articles must return up to the generic cap, not zero."""
    generics = [
        _make_article(title=f"Market news {i}", published_at="2025-10-05")
        for i in range(10)
    ]

    result = _rerank_articles(
        generics,
        ticker="AAPL",
        company_name="Apple",
        max_total=25,
        max_generic=5,
    )

    # Should not be empty (graceful fallback) and should not exceed generic cap.
    assert 1 <= len(result) <= 5, (
        f"Expected 1–5 generic articles for thin ticker; got {len(result)}"
    )


def test_specifics_exceed_total_cap_yields_zero_generics():
    """When specific articles fill the total cap, no generics should be included."""
    specifics = [
        _make_article(title=f"Apple product {i}", published_at="2025-10-05")
        for i in range(10)   # 10 specific articles
    ]
    generics = [
        _make_article(title=f"Macro roundup {i}", published_at="2025-10-05")
        for i in range(5)
    ]

    result = _rerank_articles(
        specifics + generics,
        ticker="AAPL",
        company_name="Apple",
        max_total=10,   # total cap == number of specifics
        max_generic=5,
    )

    # All 10 slots used by specifics — no room for generics.
    assert len(result) == 10, f"Expected exactly 10 articles; got {len(result)}"

    # Confirm none of the generic articles snuck in.
    generic_titles = {art["title"] for art in generics}
    result_titles  = {art["title"] for art in result}
    assert not result_titles.intersection(generic_titles), (
        "Generic articles should not appear when specifics fill the total cap"
    )


# ---------------------------------------------------------------------------
# (i) End-to-end: _build_ticker_news_context ordering
# ---------------------------------------------------------------------------

def test_build_context_respects_specificity_order():
    """Specific articles must appear before generic ones in the rendered block."""
    generic_article  = _make_article(title="Dow hits 50,000", published_at="2025-10-10")
    specific_article = _make_article(title="Apple crushes estimates", published_at="2025-10-09")

    as_of  = datetime(2025, 10, 11)
    result = _build(
        [generic_article, specific_article],
        as_of=as_of,
        ticker="AAPL",
        company_name="Apple",
    )

    idx_specific = result.index("Apple crushes estimates")
    idx_generic  = result.index("Dow hits 50,000")

    assert idx_specific < idx_generic, (
        "Specific article should appear before generic in the rendered block"
    )
