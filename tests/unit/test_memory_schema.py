from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agents.memory.schema import BufferEntry, MemoryProjection


def _entry(tag: str, summary: str = "ok", smart_money: bool = False) -> BufferEntry:
    return BufferEntry(
        timestamp=datetime.now(tz=timezone.utc),
        decision_tag=tag,
        reasoning_summary=summary,
        smart_money_seen=smart_money,
        executions_count=0,
    )


def test_buffer_entry_rejects_long_summary():
    with pytest.raises(ValidationError):
        BufferEntry(
            timestamp=datetime.now(tz=timezone.utc),
            decision_tag="x",
            reasoning_summary="x" * 121,
            smart_money_seen=False,
            executions_count=0,
        )


def test_buffer_entry_accepts_max_summary():
    e = _entry("tag", "x" * 120)
    assert len(e.reasoning_summary) == 120


def test_memory_projection_recent_limit():
    buffer = [_entry(f"tag_{i}") for i in range(12)]
    proj = MemoryProjection.from_buffer(buffer, n_recent=8)
    assert len(proj.recent) == 8
    assert proj.recent[0].decision_tag == "tag_4"  # last 8 of 12


def test_memory_projection_tag_frequency():
    buffer = [_entry("buy_aapl")] * 4 + [_entry("hold_msft")] * 2 + [_entry("sell_nvda")]
    proj = MemoryProjection.from_buffer(buffer, min_freq=3)
    assert "buy_aapl" in proj.tag_frequency
    assert "hold_msft" not in proj.tag_frequency  # only 2, below min_freq=3
    assert "sell_nvda" not in proj.tag_frequency
