"""Integration test: MemoryWriter reads/writes session state dict."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.memory.writer import MemoryWriter


@pytest.mark.asyncio
async def test_memory_writer_appends_buffer_entry():
    """Test MemoryWriter processes strategist_decision and updates state."""
    # Build a minimal mock InvocationContext
    state = {
        "strategist_decision": {
            "decision_tag": "buy_aapl",
            "reasoning": "Strong technical breakout",
            "thesis": "Bullish on tech",
            "target_weights": {},
            "confidence": 0.8,
            "new_positions": {},
            "close_reasons": {},
        },
        "memory_buffer": [],
        "day_digest": "",
        "executions": [],
    }

    # MemoryWriter now yields an Event whose ``invocation_id`` field is a
    # Pydantic-validated string, so the mock ctx must carry a real string
    # rather than the default MagicMock attribute proxy.
    session_mock = MagicMock()
    session_mock.state = state
    ctx_mock = MagicMock()
    ctx_mock.session = session_mock
    ctx_mock.invocation_id = "test-invocation"

    writer = MemoryWriter()

    # Stub embed to avoid network call.
    with patch("agents.memory.writer.embed", new=AsyncMock(return_value=[1.0, 0.0, 0.0])):
        async for _ in writer._run_async_impl(ctx_mock):
            pass  # one state_delta event expected; we just drain the generator

    buffer = state["memory_buffer"]
    assert len(buffer) == 1
    assert buffer[0]["decision_tag"] == "buy_aapl"
    assert state["thesis"] == "Bullish on tech"


@pytest.mark.asyncio
async def test_memory_writer_accepts_iso_string_as_of():
    """state["as_of"] arriving as an ISO-8601 string (from DatabaseSessionService
    JSON round-trip) must not raise and the buffer entry timestamp must match.

    Locks in the fix that dropped the ``isinstance(raw_as_of, datetime)``
    pre-filter and now passes ``raw_as_of`` directly to ``resolve_as_of``.
    """
    from datetime import datetime

    iso_as_of = "2026-05-08T14:00:00+00:00"
    state = {
        "as_of":      iso_as_of,          # ISO string, not datetime
        "strategist_decision": {
            "decision_tag":   "iso_test",
            "reasoning":      "test",
            "thesis": "Bullish",
            "target_weights": {},
            "confidence":     0.7,
            "new_positions":  {},
            "close_reasons":  {},
        },
        "memory_buffer": [],
        "day_digest":    "",
        "executions":    [],
    }

    session_mock = MagicMock()
    session_mock.state = state
    ctx_mock = MagicMock()
    ctx_mock.session = session_mock
    ctx_mock.invocation_id = "test-iso-as-of"

    writer = MemoryWriter()

    with patch("agents.memory.writer.embed", new=AsyncMock(return_value=[1.0, 0.0, 0.0])):
        async for _ in writer._run_async_impl(ctx_mock):
            pass

    buffer = state["memory_buffer"]
    assert len(buffer) == 1
    # model_dump(mode="json") serialises the datetime back to an ISO string;
    # assert it round-trips without error (not the bare string we passed in).
    ts_str = buffer[0]["timestamp"]
    assert isinstance(ts_str, str), "timestamp must be serialised as an ISO string"
    # Must parse cleanly — proving resolve_as_of produced a real datetime.
    parsed = datetime.fromisoformat(ts_str)
    expected_dt = datetime.fromisoformat(iso_as_of)
    # Compare naive datetimes (fromisoformat may vary tzinfo representation).
    assert parsed.replace(tzinfo=None) == expected_dt.replace(tzinfo=None)
