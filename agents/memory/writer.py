"""MemoryWriter — rolling buffer append + eviction, ADK BaseAgent wrapper."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from .compress import compress
from .schema import BufferEntry

BUFFER_MAX = 24
BUFFER_EVICT_AT = 25  # evict when buffer reaches this size


async def append_with_eviction(
    buffer: list[BufferEntry],
    new_entry: BufferEntry,
    day_digest: str,
    compress_fn: Callable[[str, BufferEntry, Callable | None], Awaitable[str]] | None = None,
) -> tuple[list[BufferEntry], str]:
    """Append new_entry, evicting oldest when buffer is full.

    Returns (updated_buffer, updated_day_digest).
    """
    buffer = list(buffer)
    buffer.append(new_entry)
    if len(buffer) < BUFFER_EVICT_AT:
        return buffer, day_digest
    evicted = buffer.pop(0)
    _compress = compress_fn or compress
    new_digest = await _compress(day_digest, evicted)
    return buffer, new_digest
