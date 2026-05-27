"""Memory buffer schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BufferEntry(BaseModel):
    """One tick's decision record stored in the rolling memory buffer."""

    timestamp: datetime
    decision_tag: str              # snake_case label for this tick's key decision
    reasoning_summary: str = Field(max_length=120)  # ≤120 char summary for dedup + digest
    smart_money_seen: bool         # True if any smart-money signals were present this tick
    is_repeat: bool = False        # True if dedup detected a semantically similar recent entry
    executions_count: int          # number of broker orders that filled this tick
    embedding: list[float] | None = None  # Vertex AI vector for dedup; None until computed
