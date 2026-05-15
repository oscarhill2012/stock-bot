"""MemoryWriter — rolling buffer append + eviction, ADK BaseAgent wrapper."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable
from datetime import datetime

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from data.timeguard import resolve_as_of

from .compress import compress
from .dedup import detect_repeat
from .embeddings import embed
from .schema import BufferEntry

BUFFER_MAX   = 24
BUFFER_EVICT_AT = 25  # evict the oldest entry when buffer reaches this size


def _has_real_smart_money(state: dict) -> bool:
    """Return True iff at least one smart-money evidence row has is_no_data == False.

    Handles both dict-shaped evidence (from JSON state) and Pydantic-object-shaped
    evidence (from in-process ADK pipelines), so the check is robust regardless
    of serialisation depth.

    Parameters
    ----------
    state:
        The ADK session state dict; reads ``state["smart_money_evidence"]``.

    Returns
    -------
    bool
        True if at least one evidence row reports real (non-absent) smart-money
        data; False if the list is missing, empty, or every row is no-data.
    """
    for ev in state.get("smart_money_evidence", []) or []:
        # Evidence rows may be raw dicts or Pydantic model instances.
        verdict = (
            ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)
        )
        if verdict is None:
            continue

        # Verdict itself may be a dict or a Pydantic object.
        is_no_data = (
            verdict.get("is_no_data")
            if isinstance(verdict, dict)
            else getattr(verdict, "is_no_data", False)
        )
        if not is_no_data:
            return True

    return False


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

        # Use state["as_of"] when present so memory-buffer timestamps are
        # aligned to the historical tick in backtest replay rather than
        # wall-clock time.  Falls back to wall-clock on live runs.
        raw_as_of = state.get("as_of")
        entry_ts = resolve_as_of(
            raw_as_of if isinstance(raw_as_of, datetime) else None,
            allow_wallclock=True,
            site="memory/writer",
        )

        new_entry = BufferEntry(
            timestamp=entry_ts,
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
            smart_money_seen=_has_real_smart_money(state),
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
