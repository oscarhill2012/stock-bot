from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agents.memory.schema import BufferEntry


def _entry(tag: str, summary: str = "ok", smart_money: bool = False) -> BufferEntry:
    return BufferEntry(
        timestamp=datetime.now(tz=UTC),
        decision_tag=tag,
        reasoning_summary=summary,
        smart_money_seen=smart_money,
        executions_count=0,
    )


def test_buffer_entry_rejects_long_summary():
    with pytest.raises(ValidationError):
        BufferEntry(
            timestamp=datetime.now(tz=UTC),
            decision_tag="x",
            reasoning_summary="x" * 121,
            smart_money_seen=False,
            executions_count=0,
        )


def test_buffer_entry_accepts_max_summary():
    e = _entry("tag", "x" * 120)
    assert len(e.reasoning_summary) == 120


