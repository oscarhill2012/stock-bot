"""MemoryWriter — rolling buffer append + eviction, ADK BaseAgent wrapper."""
from __future__ import annotations

from collections.abc import AsyncGenerator, Awaitable, Callable

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from data.timeguard import resolve_as_of

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

        # The cross-tick state_delta below serialises BufferEntry instances to
        # plain dicts (so ADK's InMemorySessionService can deep-copy them
        # safely).  When this agent runs on tick T+1, those dicts come back
        # in ``state["memory_buffer"]`` and must be re-hydrated to BufferEntry
        # so ``detect_repeat`` (and any other attribute-access consumer
        # downstream) sees the same shape it did before the state_delta
        # round-trip.  This mirrors the permissive-read pattern already used
        # for ``final_orders`` in ``executor/agent.py``.
        raw_buffer = state.get("memory_buffer", [])
        buffer: list[BufferEntry] = [
            BufferEntry.model_validate(e) if isinstance(e, dict) else e
            for e in raw_buffer
        ]
        day_digest: str = state.get("day_digest", "")
        executions = state.get("executions", [])

        # Use state["as_of"] when present so memory-buffer timestamps are
        # aligned to the historical tick in backtest replay rather than
        # wall-clock time.  Falls back to wall-clock on live runs.
        raw_as_of = state.get("as_of")
        entry_ts = resolve_as_of(
            raw_as_of,
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
            smart_money_seen=any(
                not (
                    (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)) is None
                    or (
                        (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)).get("is_no_data")
                        if isinstance((ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)), dict)
                        else getattr(
                            (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)),
                            "is_no_data",
                            False,
                        )
                    )
                )
                for ev in state.get("smart_money_evidence", []) or []
            ),
            executions_count=len(executions),
        )

        is_rep = await detect_repeat(new_entry, buffer, embed)
        new_entry = new_entry.model_copy(update={"is_repeat": is_rep})

        updated_buffer, updated_digest = await append_with_eviction(
            buffer, new_entry, day_digest
        )

        # Pre-compute the values once so both the in-tick direct mutation and
        # the cross-tick ``state_delta`` payload below stay consistent.
        # Use mode="json" so datetime fields (timestamp) are coerced to
        # ISO-8601 strings — DatabaseSessionService serialises state_delta
        # via json.dumps and will raise TypeError on bare datetime objects.
        memory_buffer_payload = [e.model_dump(mode="json") for e in updated_buffer]

        # NOTE: MemoryWriter no longer reads or writes the thesis at all.
        # The bare ``thesis`` state key has been fully removed; the sole
        # canonical key is ``user:thesis``, written exclusively by
        # ``_executor_thesis_writer_callback`` in executor/agent.py.
        # The ``decision.thesis`` read that used to live here is also gone —
        # the executor after-callback is the sole consumer of ``decision.thesis``
        # for the persistence path.

        # Direct mutation — visible to any later agent in *this* tick that
        # reads ``ctx.session.state`` (same object reference).
        state["memory_buffer"] = memory_buffer_payload
        state["day_digest"]    = updated_digest

        # Cross-tick propagation — ADK's ``InMemorySessionService`` only
        # merges mutations into the storage session via an Event whose
        # ``actions.state_delta`` carries them.  Without this yielded event,
        # the next tick's ``session_service.get_session`` re-fetch would
        # return a copy of storage that still has the *previous* tick's
        # ``memory_buffer`` / ``day_digest`` — i.e. the strategist's prompt
        # would see stale memory on every tick.
        #
        # This is the same pattern as the Snapshotter fix (2026-05-19).
        # The wider audit + the planned move to a DB-hydration / RAG model
        # is tracked in ``docs/todo-fixes.md`` under Group 2.5 — cross-tick
        # ADK session state propagation.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "memory_buffer": memory_buffer_payload,
                "day_digest":    updated_digest,
            }),
        )


memory_writer = MemoryWriter()
