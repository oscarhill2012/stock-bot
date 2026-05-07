"""MemoryWriter — rolling buffer append + eviction, ADK BaseAgent wrapper."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .compress import compress
from .dedup import detect_repeat
from .embeddings import embed
from .schema import BufferEntry

BUFFER_MAX   = 24
BUFFER_EVICT_AT = 25  # evict the oldest entry when buffer reaches this size


async def append_with_eviction(
    buffer: list[BufferEntry],
    new_entry: BufferEntry,
    day_digest: str,
    compress_fn: Callable[[str, BufferEntry, Callable | None], Awaitable[str]] | None = None,
) -> tuple[list[BufferEntry], str]:
    """Append new_entry to the buffer, evicting the oldest when full.

    If the buffer length reaches BUFFER_EVICT_AT, the head is popped and
    merged into `day_digest` via the compress function (LLM or fast-path).

    Returns (updated_buffer, updated_day_digest).
    """
    buffer = list(buffer)
    buffer.append(new_entry)

    if len(buffer) < BUFFER_EVICT_AT:
        return buffer, day_digest

    evicted = buffer.pop(0)
    _compress = compress_fn or compress
    new_digest = await _compress(day_digest, evicted)
    return buffer, new_digest


class MemoryWriter(BaseAgent):
    """ADK BaseAgent that appends a decision record after every tick.

    Reads the strategist decision from session state, builds a BufferEntry,
    runs semantic dedup, and calls append_with_eviction. All state mutations
    are written back into session.state — no events are emitted.
    """

    name: str = "MemoryWriter"

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        decision = state.get("strategist_decision")
        if decision is None:
            return

        buffer: list[BufferEntry] = state.get("memory_buffer", [])
        day_digest: str = state.get("day_digest", "")
        executions = state.get("executions", [])

        new_entry = BufferEntry(
            timestamp=__import__("datetime").datetime.now(
                tz=__import__("datetime").timezone.utc
            ),
            decision_tag=(
                decision.get("decision_tag", "unknown")
                if isinstance(decision, dict)
                else decision.decision_tag
            ),
            reasoning_summary=(
                decision.get("reasoning", "")[:120]
                if isinstance(decision, dict)
                else decision.reasoning[:120]
            ),
            smart_money_seen=bool(state.get("smart_money_signals")),
            executions_count=len(executions),
        )

        is_rep = await detect_repeat(new_entry, buffer, embed)
        new_entry = new_entry.model_copy(update={"is_repeat": is_rep})

        updated_buffer, updated_digest = await append_with_eviction(
            buffer, new_entry, day_digest
        )

        state["memory_buffer"] = [e.model_dump() for e in updated_buffer]
        state["day_digest"] = updated_digest

        if isinstance(decision, dict):
            state["thesis"] = decision.get("updated_thesis", state.get("thesis", ""))
        else:
            state["thesis"] = decision.updated_thesis

        # No events to yield — pure state mutation.
        return
        yield  # required to make this an async generator


memory_writer = MemoryWriter()
