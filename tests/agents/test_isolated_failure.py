# tests/agents/test_isolated_failure.py
"""Unit tests for IsolatedFailureWrapper — confirms failure-containment semantics."""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService, Session

from agents.isolated_failure import IsolatedFailureWrapper


class _OkAgent(BaseAgent):
    """Inner agent that yields one event and returns normally."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        yield Event(
            author=self.name,
            invocation_id=ctx.invocation_id,
            actions=EventActions(state_delta={"ok_key": "ok_value"}),
        )


class _BoomAgent(BaseAgent):
    """Inner agent that raises mid-run."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        raise RuntimeError("simulated branch failure")
        yield  # pragma: no cover — unreachable, kept for generator typing


@pytest.mark.asyncio
async def test_ok_path_forwards_inner_events(_invocation_context):
    """When the inner runs cleanly, every inner event is forwarded unchanged."""

    wrapper = IsolatedFailureWrapper(
        name="TestWrapper",
        inner=_OkAgent(name="Inner"),
        analyst="news",
        ticker="AAPL",
    )

    events = [ev async for ev in wrapper.run_async(_invocation_context)]

    assert len(events) == 1
    assert events[0].actions.state_delta == {"ok_key": "ok_value"}


@pytest.mark.asyncio
async def test_failure_is_swallowed_and_logged(_invocation_context, caplog):
    """When the inner raises, the wrapper yields zero events and logs the failure."""

    wrapper = IsolatedFailureWrapper(
        name="TestWrapper",
        inner=_BoomAgent(name="Inner"),
        analyst="news",
        ticker="AAPL",
    )

    with caplog.at_level(logging.WARNING, logger="agents.isolated_failure"):
        events = [ev async for ev in wrapper.run_async(_invocation_context)]

    assert events == []
    # Structured failure log was emitted.
    failure_records = [r for r in caplog.records if getattr(r, "kind", None) == "branch_failed"]
    assert len(failure_records) == 1
    rec = failure_records[0]
    assert rec.analyst == "news"
    assert rec.ticker == "AAPL"
    assert rec.exc_type == "RuntimeError"
    assert "simulated branch failure" in rec.exc_message


@pytest.fixture
def _invocation_context():
    """Minimal InvocationContext stub for BaseAgent.run_async.

    Deviation from the original plan: the plan constructed the Session via
    ``InMemorySessionService.create_session`` (which is async) using
    ``asyncio.get_event_loop().run_until_complete`` inside a sync fixture.
    That pattern is unreliable with pytest-asyncio 1.3.0 because
    ``asyncio.get_event_loop()`` may return a different loop (or a closed one)
    from the one pytest-asyncio provisions for the test coroutine.

    Instead, we construct ``Session`` directly (it is a plain Pydantic model
    that does not require any async initialisation) and pass it alongside a
    fresh ``InMemorySessionService``.  The ``agent`` field is required by
    ``InvocationContext``; ``BaseAgent.run_async`` always overwrites it via
    ``model_copy(update={'agent': self})`` before calling ``_run_async_impl``,
    so the placeholder value here does not affect test behaviour.
    """

    # _PlaceholderAgent exists solely to satisfy the non-Optional `agent`
    # field on InvocationContext.  It is never executed — run_async replaces
    # it with the real wrapper instance before calling _run_async_impl.
    class _PlaceholderAgent(BaseAgent):
        async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
            return  # pragma: no cover
            yield  # pragma: no cover — makes this an async generator

    svc = InMemorySessionService()
    session = Session(
        id="t1",
        app_name="test",
        user_id="test",
        state={},
        events=[],
    )

    return InvocationContext(
        session_service=svc,
        session=session,
        invocation_id="inv-1",
        agent=_PlaceholderAgent(name="placeholder"),
    )
