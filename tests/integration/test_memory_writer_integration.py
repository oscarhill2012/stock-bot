"""Integration test: MemoryWriter reads/writes session state dict."""
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from agents.memory.writer import MemoryWriter


@pytest.mark.asyncio
async def test_memory_writer_appends_buffer_entry():
    """Test MemoryWriter processes strategist_decision and updates state."""
    # Build a minimal mock InvocationContext
    state = {
        "strategist_decision": {
            "decision_tag": "buy_aapl",
            "reasoning": "Strong technical breakout",
            "updated_thesis": "Bullish on tech",
            "target_weights": {},
            "confidence": 0.8,
            "new_positions": {},
            "close_reasons": {},
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
    }

    session_mock = MagicMock()
    session_mock.state = state
    ctx_mock = MagicMock()
    ctx_mock.session = session_mock

    writer = MemoryWriter()

    # Stub embed to avoid network call
    with patch("agents.memory.writer.embed", new=AsyncMock(return_value=[1.0, 0.0, 0.0])):
        async for _ in writer._run_async_impl(ctx_mock):
            pass  # no events expected

    buffer = state["memory_buffer"]
    assert len(buffer) == 1
    assert buffer[0]["decision_tag"] == "buy_aapl"
    assert state["thesis"] == "Bullish on tech"
