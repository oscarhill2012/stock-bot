"""Per-run news-history store backing the staleness pre-filter (Phase 14).

Implements the Tetlock textual-similarity measure deterministically: each
article seen by the news subsystem is embedded once and recorded under a
namespace; new candidate text is scored by its maximum cosine similarity
against everything already recorded in that namespace.  Scores at or above
``staleness_similarity_threshold`` (config/analysts.json) mark an article
as a stale rehash rather than a fresh surprise.

Namespaces are plain strings.  This plan uses ticker symbols (company
news); Plan 5's macro analyst uses the reserved namespace ``"macro"``.

Lifecycle — PIT correctness (spec D2):
    The store is strictly PER-RUN state.  Live trading accumulates it
    within a single process run; the backtest driver calls
    ``reset_news_history_store()`` at the start of every window replay so
    the store is rebuilt tick-by-tick from the golden cache's news
    timeline.  It is NEVER persisted to disk and never survives across
    windows — an article from window B must not look "previously seen"
    because window A mentioned it.

Failure policy:
    Embedding failures RAISE.  A store that silently treats articles as
    fresh (or stale) when the embedding backend is down is exactly the
    silent-degradation bug class this project bans.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from agents.memory.embeddings import cosine_similarity, embed

# Type alias for the injectable embedding function (tests supply stubs).
EmbedFn = Callable[[str], Awaitable[list[float]]]


@dataclass
class _HistoryEntry:
    """One recorded article: its identity key, embedding, and timestamp.

    Attributes:
        article_key:  stable identity (URL or content digest) — dedup handle.
        vector:       embedding of the article's headline + capped summary.
        published_at: publication time, kept for future window-decay logic.
    """

    article_key: str
    vector: list[float]
    published_at: datetime


class NewsHistoryStore:
    """In-memory, per-run store of article embeddings, keyed by namespace.

    Parameters:
        embed_fn: async ``text -> vector`` function.  Defaults to the shared
                  memory-subsystem embedder; tests inject deterministic stubs.
    """

    def __init__(self, embed_fn: EmbedFn = embed) -> None:
        """Initialise an empty store with the given embedding function."""
        self._embed_fn: EmbedFn = embed_fn

        # namespace → ordered list of recorded entries.
        self._entries: dict[str, list[_HistoryEntry]] = {}

        # namespace → set of recorded article keys (O(1) identity checks).
        self._keys: dict[str, set[str]] = {}

    def has(self, namespace: str, article_key: str) -> bool:
        """Return True if ``article_key`` was already recorded in ``namespace``.

        This is the exact-identity short-circuit: a re-fetched article whose
        key is known needs no embedding call — it is stale by definition.

        Parameters:
            namespace:   history partition (ticker symbol, or "macro").
            article_key: stable identity key from ``fetch.article_key``.

        Returns:
            Whether the key is present in the namespace.
        """
        return article_key in self._keys.get(namespace, set())

    async def staleness(self, namespace: str, text: str) -> float:
        """Score how textually stale ``text`` is within ``namespace``.

        Embeds the candidate text and returns its maximum cosine similarity
        against every entry recorded in the namespace — the deterministic
        Tetlock stale-news measure.  An empty namespace short-circuits to
        0.0 without spending an embedding call.

        Parameters:
            namespace: history partition (ticker symbol, or "macro").
            text:      candidate article text (headline + capped summary).

        Returns:
            Max cosine similarity in [0.0, 1.0]; 0.0 when nothing is recorded.

        Raises:
            Whatever the embedding function raises — failures are loud.
        """
        entries = self._entries.get(namespace)
        if not entries:
            return 0.0

        candidate = await self._embed_fn(text)

        return max(
            cosine_similarity(candidate, entry.vector) for entry in entries
        )

    async def record(
        self,
        namespace: str,
        article_key: str,
        text: str,
        published_at: datetime,
    ) -> None:
        """Embed ``text`` and record it under ``namespace``.

        Idempotent per ``(namespace, article_key)`` — recording a key that
        is already present is a no-op, so each article costs at most one
        embedding call for the lifetime of the run.

        Parameters:
            namespace:    history partition (ticker symbol, or "macro").
            article_key:  stable identity key from ``fetch.article_key``.
            text:         article text to embed (headline + capped summary).
            published_at: the article's publication time.

        Returns:
            None.

        Raises:
            Whatever the embedding function raises — failures are loud.
        """
        if self.has(namespace, article_key):
            return

        vector = await self._embed_fn(text)

        self._entries.setdefault(namespace, []).append(
            _HistoryEntry(
                article_key=article_key,
                vector=vector,
                published_at=published_at,
            )
        )
        self._keys.setdefault(namespace, set()).add(article_key)


# ── Module-level per-run singleton ────────────────────────────────────────
#
# The fetch agent (and Plan 5's macro analyst) share one store per process
# run.  The backtest driver resets it at the start of every window replay.

_STORE: NewsHistoryStore | None = None


def get_news_history_store() -> NewsHistoryStore:
    """Return the process-wide per-run store, creating it on first use.

    Returns:
        The shared ``NewsHistoryStore`` instance for the current run.
    """
    global _STORE
    if _STORE is None:
        _STORE = NewsHistoryStore()
    return _STORE


def reset_news_history_store() -> None:
    """Discard the current store so the next access builds a fresh one.

    Called by the backtest driver before each window replay — history must
    be rebuilt from that window's golden-cache news timeline, never carried
    over from a previous window or run (PIT correctness, spec D2).

    Returns:
        None.
    """
    global _STORE
    _STORE = None
