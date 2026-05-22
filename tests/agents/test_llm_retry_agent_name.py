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

import logging
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event

from agents.llm_retry import RetryingAgentWrapper
from config.llm_retry import RetryConfig


class _FlakyAgent(BaseAgent):
    """Test double that raises one retryable exception then succeeds.

    Mirrors the shape of a real LlmAgent for the retry wrapper's
    perspective — it exposes ``.name`` and ``.run_async`` and yields one
    ``Event`` on success.
    """

    name: str = "TestAnalyst"

    async def _run_async_impl(self, ctx) -> AsyncGenerator[Event, None]:
        if not getattr(self, "_raised", False):
            self._raised = True
            # A pydantic.ValidationError-shape exception classified as
            # retryable by ``_is_retryable``.  Using ImportError-free
            # construction so the test does not pull in pydantic
            # internals.
            from pydantic import ValidationError
            try:
                from pydantic import BaseModel, Field
                class _S(BaseModel):
                    x: int = Field(ge=0)
                _S.model_validate({"x": -1})
            except ValidationError as exc:
                raise exc

        yield Event(author=self.name)


pytestmark = pytest.mark.asyncio


async def test_retry_warning_includes_inner_agent_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The before_sleep hook attributes each retry to the wrapped agent."""

    cfg = RetryConfig(
        max_attempts        = 3,
        base_delay_seconds  = 0.001,
        max_delay_seconds   = 0.01,
    )
    wrapper = RetryingAgentWrapper(
        inner         = _FlakyAgent(),
        retry_config  = cfg,
    )

    caplog.set_level(logging.WARNING, logger="agents.llm_retry")

    # Run the wrapper; consume the (empty) async-gen so the retry loop
    # actually executes.  The ctx argument is replaced with a stub since
    # this test only exercises the retry wrapper's behaviour.
    async for _ in wrapper.run_async(ctx=None):  # type: ignore[arg-type]
        pass

    retry_records = [
        r for r in caplog.records
        if r.name == "agents.llm_retry" and r.levelno == logging.WARNING
    ]
    assert retry_records, "expected at least one retry-warning log record"
    assert any("TestAnalyst" in r.getMessage() for r in retry_records), (
        f"no retry record carried the wrapped agent name; messages were: "
        f"{[r.getMessage() for r in retry_records]}"
    )
