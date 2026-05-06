import pytest
from datetime import datetime, timezone

from agents.memory.compress import compress
from agents.memory.schema import BufferEntry


def _entry(tag: str, summary: str) -> BufferEntry:
    return BufferEntry(
        timestamp=datetime.now(tz=timezone.utc),
        decision_tag=tag,
        reasoning_summary=summary[:120],
        smart_money_seen=False,
        executions_count=0,
    )


async def _stub_llm(prev: str, new: str) -> str:
    return f"COMPRESSED: {new[:50]}"


@pytest.mark.asyncio
async def test_compress_concat_when_small():
    prev = "Previous log entry"
    entry = _entry("buy_aapl", "Bought AAPL on breakout")
    result = await compress(prev, entry, llm_fn=_stub_llm)
    assert "buy_aapl" in result
    assert "Bought AAPL on breakout" in result
    assert len(result) <= 2000


@pytest.mark.asyncio
async def test_compress_calls_llm_when_large():
    prev = "x" * 1980  # near budget
    entry = _entry("sell_msft", "Sold MSFT on earnings miss")
    result = await compress(prev, entry, llm_fn=_stub_llm)
    assert result.startswith("COMPRESSED:")
    assert len(result) <= 2000


@pytest.mark.asyncio
async def test_compress_result_under_budget():
    prev = "short log"
    entry = _entry("hold_nvda", "Holding NVDA")
    result = await compress(prev, entry, llm_fn=_stub_llm)
    assert len(result) <= 2000
