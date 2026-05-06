"""Semantic dedup — detect repeated decision patterns."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from .embeddings import cosine_similarity
from .schema import BufferEntry

REPEAT_WINDOW = 4
COSINE_THRESHOLD = 0.85


async def detect_repeat(
    new_entry: BufferEntry,
    recent_buffer: list[BufferEntry],
    embed_fn: Callable[[str], Awaitable[list[float]]],
) -> bool:
    """Return True if new_entry is semantically similar to a recent same-tag entry."""
    window = recent_buffer[-REPEAT_WINDOW:]
    tag_matches = [e for e in window if e.decision_tag == new_entry.decision_tag]
    if not tag_matches:
        return False
    new_vec = await embed_fn(new_entry.reasoning_summary)
    for match in tag_matches:
        if match.embedding is None:
            match_vec = await embed_fn(match.reasoning_summary)
        else:
            match_vec = match.embedding
        if cosine_similarity(new_vec, match_vec) >= COSINE_THRESHOLD:
            return True
    return False
