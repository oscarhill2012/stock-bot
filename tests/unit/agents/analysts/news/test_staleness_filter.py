"""Tests for the deterministic embedding staleness pre-filter (Phase 14).

Covers ``article_key`` (stable identity) and
``partition_articles_by_staleness`` (fresh/stale split), which together
replace the deleted heuristic specificity re-ranker.  Embeddings are
stubbed with deterministic vectors — no network access.
"""
from __future__ import annotations

import pytest

from agents.analysts.news.fetch import article_key, partition_articles_by_staleness
from agents.analysts.news.history import NewsHistoryStore


def _stub_embed_factory(calls: list[str]):
    """Deterministic embed stub — see test_history.py for the vector design.

    Parameters:
        calls: list mutated in place with each embedded text.

    Returns:
        An async ``embed_fn(text) -> list[float]``.
    """
    async def _stub(text: str) -> list[float]:
        calls.append(text)
        if "beats on earnings" in text:
            return [1.0, 0.0, 0.0]
        if "tops earnings estimates" in text:
            return [0.97, 0.24, 0.0]
        if "layoffs" in text:
            return [0.0, 1.0, 0.0]
        raise AssertionError(f"unexpected embed text: {text!r}")

    return _stub


def _article(title: str, url: str, published: str) -> dict:
    """Build a serialised article dict in the provider shape."""
    return {"title": title, "summary": "", "url": url, "published_at": published}


@pytest.mark.asyncio
async def test_known_stale_rehash_is_filtered_within_one_tick():
    """A same-tick paraphrase of an earlier article lands in the stale
    bucket; the original survives as fresh.  POSITIVE assertions both ways."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    original = _article("AAPL beats on earnings",
                        "https://news/a1", "2026-07-01T12:00:00")
    rehash = _article("Apple tops earnings estimates",
                      "https://news/b2", "2026-07-01T13:00:00")

    # Deliberately pass the rehash FIRST — oldest-first processing must
    # still judge the original before the rehash.
    fresh, stale = await partition_articles_by_staleness(
        "AAPL", [rehash, original], store=store, threshold=0.85,
    )

    assert fresh == [original]
    assert stale == [rehash]


@pytest.mark.asyncio
async def test_article_is_stale_on_the_next_tick_without_reembedding():
    """A re-fetched article is stale by identity — no second embed call."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    fresh_1, stale_1 = await partition_articles_by_staleness(
        "AAPL", [art], store=store, threshold=0.85,
    )
    assert fresh_1 == [art] and stale_1 == []

    embeds_after_first_tick = len(calls)

    fresh_2, stale_2 = await partition_articles_by_staleness(
        "AAPL", [art], store=store, threshold=0.85,
    )

    assert stale_2 == [art] and fresh_2 == []
    assert len(calls) == embeds_after_first_tick    # has() short-circuit held


@pytest.mark.asyncio
async def test_genuinely_novel_stories_stay_fresh():
    """Dissimilar stories all pass, returned oldest-first."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    earnings = _article("AAPL beats on earnings",
                        "https://news/a1", "2026-07-01T12:00:00")
    layoffs = _article("AAPL layoffs announced",
                       "https://news/a2", "2026-07-02T09:00:00")

    fresh, stale = await partition_articles_by_staleness(
        "AAPL", [layoffs, earnings], store=store, threshold=0.85,
    )

    assert fresh == [earnings, layoffs]
    assert stale == []


@pytest.mark.asyncio
async def test_partition_namespaces_do_not_cross_contaminate():
    """AAPL history must not make the same story stale for MSFT."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    await partition_articles_by_staleness("AAPL", [art], store=store, threshold=0.85)

    # Different URL so the identity short-circuit cannot fire either.
    msft_copy = _article("AAPL beats on earnings",
                         "https://news/z9", "2026-07-01T12:00:00")
    fresh, stale = await partition_articles_by_staleness(
        "MSFT", [msft_copy], store=store, threshold=0.85,
    )

    assert fresh == [msft_copy] and stale == []


@pytest.mark.asyncio
async def test_embedding_failure_propagates():
    """Embedding outages fail the partition loudly — no silent 'fresh'."""
    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)
    art = _article("AAPL beats on earnings",
                   "https://news/a1", "2026-07-01T12:00:00")

    with pytest.raises(RuntimeError, match="embedding endpoint down"):
        await partition_articles_by_staleness(
            "AAPL", [art], store=store, threshold=0.85,
        )


def test_article_key_prefers_url_and_falls_back_to_a_digest():
    """URL is the identity when present; otherwise headline+timestamp digest
    (so two same-headline stories on different days do not collide)."""
    with_url = {"title": "T", "url": "https://news/x1", "published_at": "2026-07-01"}
    assert article_key(with_url) == "https://news/x1"

    no_url_day1 = {"title": "Same headline", "published_at": "2026-07-01"}
    no_url_day2 = {"title": "Same headline", "published_at": "2026-07-02"}

    assert article_key(no_url_day1).startswith("hash:")
    assert article_key(no_url_day1) != article_key(no_url_day2)
    assert article_key(no_url_day1) == article_key(dict(no_url_day1))   # stable
