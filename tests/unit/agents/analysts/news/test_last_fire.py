"""Unit tests for the per-run news last-fire store (Phase 14, Plan 3c Task 9).

Covers the record/get roundtrip, overwrite-on-new-fire semantics, the
module-level singleton reset discipline, and the absent-ticker miss case.
Task 10 will consume this store to carry a decayed catalyst on subsequent
abstain ticks — these tests only cover the store itself.
"""
from __future__ import annotations


def test_record_and_get_roundtrip():
    """A recorded fire is retrievable verbatim, including the ISO fired_at."""
    from agents.analysts.news.last_fire import NewsLastFireStore

    store = NewsLastFireStore()
    store.record(
        "STLD", lean="bullish", magnitude=0.7, confidence=0.8,
        fired_at="2025-09-17T14:00:00",
    )
    rec = store.get("STLD")

    assert rec.lean == "bullish" and rec.magnitude == 0.7
    assert rec.fired_at == "2025-09-17T14:00:00"


def test_new_fire_overwrites():
    """Recording a second fire for the same ticker replaces the first."""
    from agents.analysts.news.last_fire import NewsLastFireStore

    store = NewsLastFireStore()
    store.record(
        "STLD", lean="bullish", magnitude=0.7, confidence=0.8,
        fired_at="2025-09-17T14:00:00",
    )
    store.record(
        "STLD", lean="bearish", magnitude=0.5, confidence=0.6,
        fired_at="2025-09-25T14:00:00",
    )

    assert store.get("STLD").lean == "bearish"


def test_reset_swaps_instance():
    """Resetting the module singleton discards the old store on next access."""
    from agents.analysts.news.last_fire import (
        get_news_last_fire_store,
        reset_news_last_fire_store,
    )

    first = get_news_last_fire_store()
    reset_news_last_fire_store()

    assert get_news_last_fire_store() is not first

    # Leave the module state clean for other tests in the process.
    reset_news_last_fire_store()


def test_get_absent_ticker_returns_none():
    """Looking up a ticker that never fired returns None, not a KeyError."""
    from agents.analysts.news.last_fire import NewsLastFireStore

    assert NewsLastFireStore().get("NOPE") is None
