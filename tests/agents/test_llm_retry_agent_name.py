"""S9 — tenacity retry warnings include the wrapped agent's name.

The previous ``before_sleep_log(_LOGGER, logging.WARNING)`` emitted
records with no agent attribution — all 28 retry warnings in
baseline-2025-09 logged ``<unknown>`` for the agent.  Attributing
retries to News / Fundamental / Strategist required an adjacent-row
heuristic.

The fix replaces the stock helper with a small closure that captures
``self.inner.name`` at wrapper-construction time.  This test pins the
contract: the captured log record's message must contain the inner
agent's name.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.events import Event, EventActions
from google.genai.errors import ClientError

from agents.llm_retry import RetryingAgentWrapper
from config.retry_429 import Retry429Policy as RetryConfig


# ---------------------------------------------------------------------------
# Helpers — copied from tests/unit/agents/test_llm_retry.py pattern
# ---------------------------------------------------------------------------


def _make_client_error_429() -> ClientError:
    """Construct a ``ClientError`` with ``status_code=429``.

    The genai SDK's ``ClientError`` constructor is fussy about its
    arguments — we bypass it by directly setting ``status_code`` on a
    barely-initialised instance, because the retry predicate
    ``_is_resource_exhausted`` only inspects that attribute.
    """

    err            = ClientError.__new__(ClientError)
    err.status_code = 429
    err.code        = 429
    err.message     = "RESOURCE_EXHAUSTED"
    err.status      = "RESOURCE_EXHAUSTED"
    err.details     = {}
    err.response_json = {
        "error": {"code": 429, "message": "RESOURCE_EXHAUSTED"}
    }
    # BaseException requires ``args`` to be set before str() works.
    err.args = ("429 RESOURCE_EXHAUSTED",)

    return err


class _FlakyInner:
    """Test double that raises one 429 ClientError then succeeds.

    Exposes the same ``.name`` and ``.run_async`` interface that
    :class:`agents.llm_retry.RetryingAgentWrapper` depends on, without
    subclassing ``BaseAgent`` — which keeps the test free of ADK
    runner ceremony.

    The agent name ``"TestAnalyst"`` is deliberately recognisable so the
    assertion can confirm it appears in the retry warning message.
    """

    name: str = "TestAnalyst"

    def __init__(self) -> None:
        """Initialise the call counter and the single-fail flag."""

        self._call_count = 0

    async def run_async(self, _ctx: Any) -> AsyncGenerator[Event, None]:
        """Raise a 429 on the first call; yield one success event on the second.

        Parameters
        ----------
        _ctx:
            Invocation context — unused by this stub; present to match
            the ADK ``run_async`` signature.

        Yields
        ------
        Event
            A single success event on the second (and any subsequent) call.

        Raises
        ------
        ClientError
            A 429 RESOURCE_EXHAUSTED error on the very first call.
        """

        idx = self._call_count
        self._call_count += 1

        if idx == 0:
            raise _make_client_error_429()

        yield Event(
            author        = self.name,
            invocation_id = "test-invocation",
            actions       = EventActions(state_delta={"stub": "ok"}),
        )


def _fake_ctx() -> MagicMock:
    """Return a minimal MagicMock standing in for ``InvocationContext``."""

    ctx               = MagicMock()
    ctx.invocation_id = "inv-test"
    ctx.session       = MagicMock()
    ctx.session.state = {}

    return ctx


async def _drain(wrapper: RetryingAgentWrapper, ctx: Any) -> list[Event]:
    """Collect every event the wrapper yields into a list.

    Calls ``_run_async_impl`` directly — the same pattern used in
    ``tests/unit/agents/test_llm_retry.py`` — so tenacity's retry loop
    executes and the ``before_sleep`` hook fires on the first 429.
    """

    out: list[Event] = []

    async for ev in wrapper._run_async_impl(ctx):
        out.append(ev)

    return out


# ---------------------------------------------------------------------------
# Contract test
# ---------------------------------------------------------------------------


def test_retry_warning_includes_inner_agent_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The before_sleep hook attributes each retry to the wrapped agent.

    Drives the wrapper via ``_run_async_impl`` with a fake context so
    tenacity's retry loop actually executes.  After the single-retry
    run, asserts that at least one WARNING record from ``agents.llm_retry``
    exists and that its message contains ``"TestAnalyst"`` — the name
    captured from ``inner.name`` at wrapper-construction time.
    """

    cfg = RetryConfig(
        max_attempts       = 3,
        base_delay_seconds = 0.001,
        max_delay_seconds  = 0.01,
    )
    wrapper = RetryingAgentWrapper(
        inner        = _FlakyInner(),
        retry_config = cfg,
    )

    with caplog.at_level(logging.WARNING, logger="agents.llm_retry"):
        asyncio.run(_drain(wrapper, _fake_ctx()))

    retry_records = [
        r for r in caplog.records
        if r.name == "agents.llm_retry" and r.levelno == logging.WARNING
    ]

    assert retry_records, "expected at least one retry-warning log record"

    assert any("TestAnalyst" in r.getMessage() for r in retry_records), (
        "no retry record carried the wrapped agent name; messages were: "
        + str([r.getMessage() for r in retry_records])
    )
