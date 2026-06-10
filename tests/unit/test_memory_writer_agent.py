"""Unit tests for the MemoryWriter ADK agent.

These tests assert on concrete produced content — not just class identity or
a name string — so silent-degradation bugs (empty buffer, wrong tick tag,
missing reasoning summary) surface immediately.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from google.adk.agents import BaseAgent

from agents.memory.writer import MemoryWriter


def test_memory_writer_is_base_agent():
    """MemoryWriter must be an ADK BaseAgent subclass."""
    assert issubclass(MemoryWriter, BaseAgent)


def test_memory_writer_has_name():
    """MemoryWriter instance name must be the exact string ``"MemoryWriter"``."""
    mw = MemoryWriter()
    assert mw.name == "MemoryWriter"


def _make_ctx(state: dict) -> MagicMock:
    """Build a minimal mock InvocationContext for MemoryWriter.

    MemoryWriter only reads ``ctx.session.state`` and ``ctx.invocation_id``,
    so a lightweight mock is sufficient — no ADK internals needed.

    Parameters
    ----------
    state:
        The session-state dict the agent will read from and mutate.

    Returns
    -------
    MagicMock
        Mock context whose ``session.state`` is the supplied dict.
    """
    session = MagicMock()
    session.state = state
    ctx = MagicMock()
    ctx.session = session
    ctx.invocation_id = "test-invocation"
    return ctx


@pytest.mark.asyncio
async def test_memory_writer_appends_buffer_entry_with_correct_decision_tag():
    """Running MemoryWriter against a minimal tick state must append a
    ``BufferEntry`` whose ``decision_tag`` matches the strategist decision.

    This is the primary content assertion: the rolling buffer must contain a
    real entry with the expected tag, not an empty list.  A silent failure
    (e.g. the writer returns early or builds the wrong tag) would produce an
    empty or incorrectly-tagged buffer — caught here.
    """
    from agents.memory.schema import BufferEntry

    mw = MemoryWriter()

    # Minimal state with a strategist decision that has a known tag.
    state: dict = {
        "tick_id": "tick-d2-test",
        "as_of":   "2026-06-10T09:30:00+00:00",
        "strategist_decision": {
            "decision_tag": "morning_sweep",
            "reasoning":    "RSI cooled on AAPL; momentum intact on MSFT",
        },
        "memory_buffer": [],
        "day_digest":    "",
        "executions":    [],
    }

    ctx = _make_ctx(state)

    # Drive the agent to completion — collect but do not inspect events.
    async for _ in mw._run_async_impl(ctx):
        pass

    # The buffer must have grown by exactly one entry.
    buffer = state["memory_buffer"]
    assert len(buffer) == 1, (
        f"MemoryWriter must append one BufferEntry; got {len(buffer)}"
    )

    # Rehydrate to the typed schema to assert on real fields.
    entry = BufferEntry.model_validate(buffer[0])

    # Content assertion: the tag must match the strategist decision exactly.
    assert entry.decision_tag == "morning_sweep", (
        f"decision_tag mismatch: expected 'morning_sweep', got {entry.decision_tag!r}"
    )


@pytest.mark.asyncio
async def test_memory_writer_reasoning_summary_is_non_empty():
    """The ``reasoning_summary`` field on the appended BufferEntry must be a
    non-empty string sourced from the strategist decision's ``reasoning`` key.

    A blank summary is a silent-degradation mode: the strategist prompt would
    receive empty context on replay, masking the original rationale entirely.
    """
    from agents.memory.schema import BufferEntry

    mw = MemoryWriter()

    state: dict = {
        "tick_id": "tick-reasoning-test",
        "as_of":   "2026-06-10T10:00:00+00:00",
        "strategist_decision": {
            "decision_tag": "take_profit",
            # Provide a distinct reasoning string so we can confirm it was captured.
            "reasoning":    "AAPL hit target price; realising gains before earnings",
        },
        "memory_buffer": [],
        "day_digest":    "",
        "executions":    [],
    }

    ctx = _make_ctx(state)
    async for _ in mw._run_async_impl(ctx):
        pass

    buffer = state["memory_buffer"]
    assert buffer, "buffer must not be empty after MemoryWriter runs"

    entry = BufferEntry.model_validate(buffer[0])

    # Content assertion: reasoning_summary must be non-empty.
    assert entry.reasoning_summary, (
        "reasoning_summary must be a non-empty string — "
        "an empty summary silently drops context from the strategist prompt"
    )

    # The summary is the first 120 chars of the reasoning field.
    expected_prefix = "AAPL hit target price; realising gains before earnings"[:120]
    assert entry.reasoning_summary == expected_prefix, (
        f"reasoning_summary should be a 120-char truncation of the reasoning; "
        f"got {entry.reasoning_summary!r}"
    )
