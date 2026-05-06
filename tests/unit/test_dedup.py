import pytest
from datetime import datetime, timezone

from agents.memory.dedup import detect_repeat
from agents.memory.schema import BufferEntry


def _entry(tag: str, summary: str, embedding: list[float] | None = None) -> BufferEntry:
    return BufferEntry(
        timestamp=datetime.now(tz=timezone.utc),
        decision_tag=tag,
        reasoning_summary=summary[:120],
        smart_money_seen=False,
        executions_count=0,
        embedding=embedding,
    )


async def _stub_embed(text: str) -> list[float]:
    """Return same vector for 'same text', orthogonal otherwise."""
    if text == "same":
        return [1.0, 0.0, 0.0]
    return [0.0, 1.0, 0.0]


@pytest.mark.asyncio
async def test_no_tag_match_returns_false():
    new = _entry("buy_aapl", "same")
    buffer = [_entry("sell_msft", "same")]
    result = await detect_repeat(new, buffer, _stub_embed)
    assert result is False


@pytest.mark.asyncio
async def test_tag_match_high_cosine_returns_true():
    new = _entry("buy_aapl", "same")
    buffer = [_entry("buy_aapl", "same")]
    result = await detect_repeat(new, buffer, _stub_embed)
    assert result is True


@pytest.mark.asyncio
async def test_tag_match_low_cosine_returns_false():
    new = _entry("buy_aapl", "same")
    buffer = [_entry("buy_aapl", "different text")]
    result = await detect_repeat(new, buffer, _stub_embed)
    assert result is False


@pytest.mark.asyncio
async def test_uses_stored_embedding_when_present():
    new = _entry("buy_aapl", "same")
    existing = _entry("buy_aapl", "whatever", embedding=[1.0, 0.0, 0.0])
    result = await detect_repeat(new, [existing], _stub_embed)
    assert result is True
