"""Memory buffer schemas."""
from __future__ import annotations

from collections import Counter
from datetime import datetime

from pydantic import BaseModel, Field


class BufferEntry(BaseModel):
    timestamp: datetime
    decision_tag: str
    reasoning_summary: str = Field(max_length=120)
    smart_money_seen: bool
    is_repeat: bool = False
    executions_count: int
    embedding: list[float] | None = None


class MemoryProjection(BaseModel):
    """Compressed view of the buffer for injection into the strategist prompt."""
    recent: list[BufferEntry]       # last n_recent entries (default 8)
    tag_frequency: dict[str, int]   # tags with count >= min_freq (default 3)

    @classmethod
    def from_buffer(
        cls,
        buffer: list[BufferEntry],
        n_recent: int = 8,
        min_freq: int = 3,
    ) -> "MemoryProjection":
        recent = buffer[-n_recent:]
        counts = Counter(e.decision_tag for e in buffer)
        tag_frequency = {tag: count for tag, count in counts.items() if count >= min_freq}
        return cls(recent=recent, tag_frequency=tag_frequency)
