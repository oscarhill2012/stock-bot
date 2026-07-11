"""Unit tests for the news fetch helpers — Phase 14 two-section renderer.

The heuristic specificity re-ranker (``_score_article_specificity`` /
``_rerank_articles``) was replaced by the embedding staleness pre-filter
(Plan 3) and the specificity router (Plan 2); its tests were deleted with
it.  Title-dedup and recency-sort tests live in ``test_dedup_recency.py``;
staleness-partition tests live in ``test_staleness_filter.py``.  This file
covers the two-section context renderer.
"""
from __future__ import annotations

from datetime import datetime

from agents.analysts.news.fetch import _build_ticker_news_context
from config.analysts import get_analysts_config

_AS_OF = datetime(2026, 7, 6, 14, 0)


def _article(title: str, summary: str, published: str,
             url: str = "https://news/x1") -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": summary,
            "published_at": published, "url": url}


def test_fresh_articles_render_with_headline_summary_and_age():
    """Fresh (novel) articles are the surprise candidates — full render."""
    block = _build_ticker_news_context(
        "AAPL",
        [_article("AAPL beats on earnings",
                  "Strong quarter across all segments.",
                  "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )

    assert "FRESH ARTICLES" in block
    assert "AAPL beats on earnings" in block
    assert "Strong quarter across all segments." in block
    assert "1d ago" in block


def test_stale_articles_render_headline_only():
    """Previously-seen articles are drift context — headline + age, NO summary."""
    block = _build_ticker_news_context(
        "AAPL",
        [],
        [_article("AAPL beats on earnings",
                  "This summary must NOT render.",
                  "2026-07-02T12:00:00")],
        as_of=_AS_OF,
    )

    assert "PREVIOUSLY SEEN" in block
    assert "AAPL beats on earnings" in block
    assert "This summary must NOT render." not in block
    assert "4d ago" in block


def test_empty_sections_render_explicit_placeholders():
    """Both sections empty → the no-news placeholder; one section empty →
    an explicit (none) marker so the LLM never guesses."""
    empty = _build_ticker_news_context("AAPL", [], [], as_of=_AS_OF)
    assert "(no news available)" in empty
    assert "FRESH ARTICLES" not in empty

    fresh_only = _build_ticker_news_context(
        "AAPL",
        [_article("Hed", "Sum.", "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )
    assert "(none)" in fresh_only              # stale section's placeholder


def test_summaries_are_truncated_to_the_configured_cap():
    """Per-article summary text is capped at news.max_summary_chars."""
    cap = get_analysts_config().news.max_summary_chars
    block = _build_ticker_news_context(
        "AAPL",
        [_article("Hed", "x" * (cap + 500), "2026-07-05T12:00:00")],
        [],
        as_of=_AS_OF,
    )

    assert "x" * cap in block
    assert "x" * (cap + 1) not in block


def test_as_of_anchor_is_rendered():
    """The block carries the tick date so ages are self-explanatory."""
    block = _build_ticker_news_context("AAPL", [], [], as_of=_AS_OF)
    assert "As of: 2026-07-06" in block


def test_heuristic_reranker_is_fully_deleted():
    """The old scoring path must be gone, not left dormant."""
    import agents.analysts.news.fetch as fetch_mod

    for name in ("_score_article_specificity", "_rerank_articles",
                 "_build_company_terms", "_count_roundup_companies"):
        assert not hasattr(fetch_mod, name), f"{name} should have been deleted"
