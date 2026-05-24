"""End-to-end smoke test for the three-layer LLM retry.

Runs one News per-ticker branch against a fake LlmAgent that raises a
real ``pydantic.ValidationError`` on its first call and succeeds on the
second.  Asserts:

1. The wrapper produces a valid success event (after one schema retry).
2. The per-tick retry-counter accumulator
   ``temp:_obs_news_retries`` ends at ``{"schema": 1}``.

Honours the no-live-API hard rule in ``docs/test-policy.md`` — the
LlmAgent is a hand-built fake that never touches Vertex.

Note on InvocationContext construction
---------------------------------------
``BaseAgent.run_async`` calls ``ctx._create_invocation_context(ctx)``
which calls ``parent_context.model_copy(update={'agent': self})``.  A
``MagicMock(spec=InvocationContext)`` cannot satisfy this — ``model_copy``
on a spec'd mock does not copy Pydantic state.  We therefore follow the
pattern from ``tests/agents/test_isolated_failure.py`` and construct a
real ``InvocationContext`` from a real ``Session`` and
``InMemorySessionService``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.sessions import InMemorySessionService, Session
from pydantic import BaseModel, ValidationError

from agents.llm_retry import RetryingAgentWrapper, build_retry_policies


# ---------------------------------------------------------------------------
# Helpers for constructing a real ValidationError
# ---------------------------------------------------------------------------

class _Tiny(BaseModel):
    """Minimal Pydantic model used solely to construct a real ValidationError."""

    name: str


def _make_validation_error() -> ValidationError:
    """Return a real ``pydantic.ValidationError`` from a deliberately-failed parse.

    Parameters
    ----------
    (none)

    Returns
    -------
    ValidationError
        A genuine Pydantic ValidationError — not a mock — so
        ``_is_schema_error`` classifies it correctly.
    """

    try:
        _Tiny.model_validate({"name": 123})
    except ValidationError as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


# ---------------------------------------------------------------------------
# Fake inner agent
# ---------------------------------------------------------------------------

class _FakeLlmAgent(BaseAgent):
    """ADK BaseAgent that raises a real ValidationError once, then succeeds.

    The class-level ``call_count`` counter is reset to 0 at the start of
    each test so repeated runs do not bleed state.
    """

    name:       str = "FakeNewsAnalyst_AAPL"
    call_count: int = 0

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Raise ValidationError on the first call; yield a success event on the second.

        Parameters
        ----------
        ctx:
            ADK invocation context (unused — fake does not read state).

        Yields
        ------
        Event
            One event with ``verdict_present: True`` in its ``state_delta``
            on the second (and any subsequent) call.

        Raises
        ------
        pydantic.ValidationError
            On the first call only.
        """

        type(self).call_count += 1

        if type(self).call_count == 1:
            raise _make_validation_error()

        yield Event(
            author  = self.name,
            content = None,
            actions = EventActions(state_delta={"verdict_present": True}),
        )


# ---------------------------------------------------------------------------
# InvocationContext factory
# ---------------------------------------------------------------------------

class _PlaceholderAgent(BaseAgent):
    """Minimal BaseAgent to satisfy the non-Optional ``agent`` field on
    InvocationContext.  Never executed — ``run_async`` replaces it with
    the actual wrapper before calling ``_run_async_impl``.
    """

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """No-op implementation required to satisfy the abstract method."""

        return  # pragma: no cover
        yield   # pragma: no cover — makes this an async generator


def _make_invocation_context(initial_state: dict | None = None) -> InvocationContext:
    """Construct a real ``InvocationContext`` with the given initial session state.

    Uses ``InMemorySessionService`` and a directly-constructed ``Session``
    (both plain Pydantic objects that require no async initialisation).
    The ``agent`` field is populated with a placeholder — ``run_async``
    overwrites it via ``model_copy`` before each ``_run_async_impl`` call.

    Parameters
    ----------
    initial_state:
        Dict to pre-populate ``session.state`` with.  Defaults to empty.

    Returns
    -------
    InvocationContext
        A fully-populated context ready to pass to ``wrapper.run_async``.
    """

    svc     = InMemorySessionService()
    session = Session(
        id       = "smoke-test",
        app_name = "test",
        user_id  = "test",
        state    = initial_state or {},
        events   = [],
    )

    return InvocationContext(
        session_service = svc,
        session         = session,
        invocation_id   = "inv-smoke-1",
        agent           = _PlaceholderAgent(name="placeholder"),
    )


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_one_schema_retry_succeeds_and_counter_records_it() -> None:
    """One forced ValidationError + success → wrapper succeeds, counter is {"schema": 1}.

    Flow:
    1. The fake inner agent raises a real ``pydantic.ValidationError`` on attempt 1.
    2. The wrapper classifies it as ``"schema"``, emits a ``state_delta`` event
       recording ``{"schema": 1}`` in ``temp:_obs_news_retries``, and retries.
    3. On attempt 2 the fake yields a success event.
    4. The wrapper flushes the success event after exiting its retry loop.

    Assertions:
    - Exactly one event carries ``temp:_obs_news_retries`` → ``{"schema": 1}``.
    - Exactly one event carries ``verdict_present`` → ``True``.
    - ``_FakeLlmAgent.call_count`` is 2 (one fail, one succeed).
    """

    # Reset class-level counter so prior test runs do not bleed into this one.
    _FakeLlmAgent.call_count = 0

    wrapper = RetryingAgentWrapper(
        inner           = _FakeLlmAgent(),
        timeout_seconds = 5.0,
        policies        = build_retry_policies(timeout_retries=3, schema_retries=3),
        retry_state_key = "temp:_obs_news_retries",
    )

    ctx    = _make_invocation_context()
    events: list[Event] = []

    async for ev in wrapper.run_async(ctx):
        events.append(ev)

    # Partition events by what their state_delta contains.
    retry_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "temp:_obs_news_retries" in (e.actions.state_delta or {})
    ]
    success_evs = [
        e for e in events
        if e.actions and e.actions.state_delta
        and "verdict_present" in (e.actions.state_delta or {})
    ]

    # One retry was performed → one counter event.
    assert len(retry_evs) == 1, (
        f"Expected exactly 1 retry-counter event, got {len(retry_evs)}: {retry_evs}"
    )

    # The counter must record exactly one schema retry.
    assert retry_evs[0].actions.state_delta["temp:_obs_news_retries"] == {"schema": 1}, (
        f"Unexpected counter value: {retry_evs[0].actions.state_delta}"
    )

    # The wrapper flushed the success event from the second attempt.
    assert len(success_evs) == 1, (
        f"Expected exactly 1 success event, got {len(success_evs)}: {success_evs}"
    )

    # The fake was called twice — once failing, once succeeding.
    assert _FakeLlmAgent.call_count == 2, (
        f"Expected 2 calls to FakeLlmAgent, got {_FakeLlmAgent.call_count}"
    )
