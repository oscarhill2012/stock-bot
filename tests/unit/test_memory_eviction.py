import pytest
from datetime import datetime, timezone

from agents.memory.writer import BUFFER_EVICT_AT, append_with_eviction
from agents.memory.schema import BufferEntry


def _entry(tag: str = "hold") -> BufferEntry:
    return BufferEntry(
        timestamp=datetime.now(tz=timezone.utc),
        decision_tag=tag,
        reasoning_summary="some reasoning",
        smart_money_seen=False,
        executions_count=0,
    )


async def _stub_compress(prev: str, evicted: BufferEntry, llm_fn=None) -> str:
    return f"digest+{evicted.decision_tag}"


@pytest.mark.asyncio
async def test_append_no_eviction_below_max():
    buffer = [_entry() for _ in range(23)]
    new = _entry("new_tag")
    result, digest = await append_with_eviction(buffer, new, "old_digest", _stub_compress)
    assert len(result) == 24
    assert digest == "old_digest"


@pytest.mark.asyncio
async def test_append_evicts_oldest_at_max():
    buffer = [_entry(f"tag_{i}") for i in range(BUFFER_EVICT_AT - 1)]
    new = _entry("new_tag")
    result, digest = await append_with_eviction(buffer, new, "old_digest", _stub_compress)
    assert len(result) == BUFFER_EVICT_AT - 1
    assert result[0].decision_tag == "tag_1"  # tag_0 evicted
    assert result[-1].decision_tag == "new_tag"


@pytest.mark.asyncio
async def test_evicted_entry_passed_to_compress():
    buffer = [_entry(f"tag_{i}") for i in range(BUFFER_EVICT_AT - 1)]
    new = _entry("newest")
    result, digest = await append_with_eviction(buffer, new, "", _stub_compress)
    assert digest == "digest+tag_0"
