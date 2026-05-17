"""Unit tests for Phase 1 extensions to ``NewsArticle``."""
from __future__ import annotations

from datetime import UTC, datetime

from data.models.news import NewsArticle


def test_news_article_relevance_optional() -> None:
    """Relevance field is accepted when explicitly set."""
    a = NewsArticle(
        ticker="AAPL", headline="x", url="https://x",
        source="alpha_vantage",
        published_at=datetime(2023, 3, 10, tzinfo=UTC),
        sentiment=0.4, relevance=0.85,
    )
    assert a.relevance == 0.85


def test_news_article_relevance_default_none() -> None:
    """Relevance defaults to None — back-compat with Finnhub/other providers."""
    a = NewsArticle(
        ticker="AAPL", headline="x", url="https://x", source="finnhub",
        published_at=datetime(2023, 3, 10, tzinfo=UTC),
    )
    assert a.relevance is None


def test_news_article_relevance_round_trip() -> None:
    """Relevance field survives model_dump → model_validate round-trip."""
    a = NewsArticle(
        ticker="MSFT", headline="y", url="https://y", source="alpha_vantage",
        published_at=datetime(2023, 3, 10, tzinfo=UTC),
        relevance=0.62,
    )
    restored = NewsArticle.model_validate(a.model_dump())
    assert restored == a
