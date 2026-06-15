"""Unit tests for ``_build_ticker_news_context`` and ``_parse_published``
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
"""
from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from agents.analysts.news.fetch import _build_ticker_news_context, _parse_published

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


def _build(articles: list, *, as_of: datetime, ticker: str = "AAPL") -> str:
    """Call ``_build_ticker_news_context`` with config caps patched to sane values.

    Uses a ``MagicMock`` instead of constructing a real ``NewsCaps`` object
    so the test is insulated from changes to ``NewsCaps`` field requirements.
    The mock exposes only the two fields actually read by
    ``_build_ticker_news_context``: ``max_articles_per_ticker`` and
    ``max_summary_chars``.
    """
    from unittest.mock import MagicMock

    fake_caps = MagicMock()
    fake_caps.max_articles_per_ticker = 10
    fake_caps.max_summary_chars = 200

    with patch("agents.analysts.news.fetch._caps", return_value=fake_caps):
        return _build_ticker_news_context(ticker, articles, as_of=as_of)


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
