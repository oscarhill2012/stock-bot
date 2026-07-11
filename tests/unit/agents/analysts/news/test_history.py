"""Unit tests for the per-run NewsHistoryStore (Phase 14 Plan 3).

The store backs the deterministic embedding staleness pre-filter: it holds
one embedding vector per previously-seen article, per namespace, and
answers "how similar is this new text to anything already recorded?".
All embedding calls are stubbed — no network access in unit tests.
"""
from __future__ import annotations

from datetime import datetime

import pytest

import agents.analysts.news.history as history
from agents.analysts.news.history import (
    NewsHistoryStore,
    get_news_history_store,
    reset_news_history_store,
)

_PUBLISHED = datetime(2026, 7, 1, 12, 0)


def _stub_embed_factory(calls: list[str]):
    """Build a deterministic embed stub that logs every text it embeds.

    Vectors are chosen so that the two 'earnings' phrasings are near-parallel
    (cosine ≈ 0.97 — a syndicated rehash) while 'layoffs' is orthogonal to
    both (a genuinely novel story).

    Parameters:
        calls: list mutated in place with each embedded text (call log).

    Returns:
        An async ``embed_fn(text) -> list[float]`` suitable for the store.
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


@pytest.mark.asyncio
async def test_staleness_is_zero_for_an_empty_namespace():
    """With nothing recorded, everything is maximally novel — and no
    embedding call is spent finding that out."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))

    similarity = await store.staleness("AAPL", "AAPL beats on earnings")

    assert similarity == 0.0
    assert calls == []          # short-circuit: no embed for an empty namespace


@pytest.mark.asyncio
async def test_staleness_is_high_for_a_recorded_rehash():
    """A paraphrased rehash of a recorded story scores near 1.0."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("AAPL", "Apple tops earnings estimates")

    assert similarity > 0.9     # POSITIVE assertion — the rehash IS caught


@pytest.mark.asyncio
async def test_staleness_is_low_for_a_novel_story():
    """An unrelated story scores near 0 against the recorded history."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("AAPL", "AAPL layoffs announced")

    assert similarity < 0.1


@pytest.mark.asyncio
async def test_namespaces_are_isolated():
    """AAPL's history must never make an MSFT article look stale."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    similarity = await store.staleness("MSFT", "AAPL beats on earnings")

    assert similarity == 0.0    # MSFT namespace is empty


@pytest.mark.asyncio
async def test_has_tracks_recorded_article_keys_per_namespace():
    """has() gives an exact-identity short-circuit, scoped to the namespace."""
    store = NewsHistoryStore(embed_fn=_stub_embed_factory([]))
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    assert store.has("AAPL", "url-1") is True
    assert store.has("AAPL", "url-2") is False
    assert store.has("MSFT", "url-1") is False


@pytest.mark.asyncio
async def test_recording_the_same_key_twice_does_not_reembed():
    """record() is idempotent per (namespace, key) — one embed per article."""
    calls: list[str] = []
    store = NewsHistoryStore(embed_fn=_stub_embed_factory(calls))

    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)
    await store.record("AAPL", "url-1", "AAPL beats on earnings",
                       published_at=_PUBLISHED)

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_embedding_failure_raises_loudly():
    """An embedding outage must fail the run, never silently mark articles
    fresh (or stale) — silent degradation is this project's banned bug class."""
    async def _broken(text: str) -> list[float]:
        raise RuntimeError("embedding endpoint down")

    store = NewsHistoryStore(embed_fn=_broken)

    with pytest.raises(RuntimeError, match="embedding endpoint down"):
        await store.record("AAPL", "url-1", "AAPL beats on earnings",
                           published_at=_PUBLISHED)


def test_module_singleton_reset_swaps_the_instance():
    """reset_news_history_store() must hand back a brand-new empty store —
    the backtest driver relies on this for per-run PIT isolation."""
    first = get_news_history_store()
    reset_news_history_store()
    second = get_news_history_store()

    assert second is not first
    # Reset again so this test leaves no shared state behind.
    reset_news_history_store()
