"""Unit tests for :class:`agents.llm_retry.RetryingAgentWrapper`.

Four scenarios covered:

1. **Happy path** — the inner succeeds on its first attempt; every event it
   yields is forwarded in order; ``run_async`` is invoked exactly once.
2. **Transient 429** — the inner raises a 429 twice and then succeeds; the
   wrapper invokes the inner three times, yields only the events from the
   successful attempt, and discards the partial yields from the failed
   attempts.
3. **Persistent 429** — the inner raises 429 forever; the wrapper invokes
   the inner exactly ``max_attempts`` times and re-raises the original
   exception (preserved via ``reraise=True``).
4. **Non-retryable error** — the inner raises a ``ValueError``; the
   wrapper propagates it immediately on the first attempt without
   sleeping.

Each test injects a hand-built :class:`config.llm_retry.RetryConfig` with
sub-second delays so the suite stays fast (``base_delay_seconds=0.001``).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.events import Event, EventActions
from google.genai.errors import ClientError

from agents.llm_retry import RetryingAgentWrapper, _is_resource_exhausted
from config.llm_retry import RetryConfig

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _fast_config(*, max_attempts: int = 5) -> RetryConfig:
    """Build a :class:`RetryConfig` with negligible delays for unit tests.

    The retry wrapper sleeps between attempts in production; using the
    production defaults (base_delay=2s, max_delay=30s) would make this
    test file take several minutes to run even on the happy path because
    tenacity still applies its own internal scheduling.
    """

    return RetryConfig(
        max_attempts       = max_attempts,
        base_delay_seconds = 0.001,
        max_delay_seconds  = 0.001,
    )


def _make_client_error_429() -> ClientError:
    """Construct a ``ClientError`` with ``status_code=429``.

    The genai SDK's ``ClientError`` constructor is fussy about its
    arguments — it expects a response body and an httpx response.  We
    cheat by directly setting ``status_code`` on a barely-initialised
    instance because the wrapper's detection only looks at that
    attribute.
    """

    err = ClientError.__new__(ClientError)
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


class _FakeInner:
    """A scriptable BaseAgent stand-in.

    Constructed with a list of attempt outcomes — each outcome is either
    ``None`` (succeed and yield the canned events) or an exception
    instance (raise that exception).  The first call to ``run_async``
    consumes outcomes[0], the second consumes outcomes[1], and so on.
    Records the number of times it was invoked on ``call_count``.
    """

    name: str = "FakeInner"

    def __init__(
        self,
        outcomes: list[BaseException | None],
        events_per_success: list[Event] | None = None,
    ) -> None:
        self._outcomes  = list(outcomes)
        self._events    = events_per_success or [
            Event(
                author        = "FakeInner",
                invocation_id = "test-invocation",
                actions       = EventActions(state_delta={"fake": "ok"}),
            ),
        ]
        self.call_count = 0

    async def run_async(self, _ctx: Any) -> AsyncGenerator[Event, None]:
        """Pop the next outcome and either raise or yield canned events."""

        # Capture which attempt this is, then advance the counter so a
        # subsequent retry consumes the next scripted outcome.
        idx = self.call_count
        self.call_count += 1

        # If we've run out of scripted outcomes, default to success —
        # avoids IndexError in the "succeeds first try" test where
        # outcomes=[] is conceptually equivalent to "no failures planned".
        outcome = self._outcomes[idx] if idx < len(self._outcomes) else None

        if isinstance(outcome, BaseException):
            raise outcome

        # Yield each canned event.  ADK agents normally yield as they go,
        # so this loop matches that shape.
        for ev in self._events:
            yield ev


def _fake_ctx() -> MagicMock:
    """Return a minimal MagicMock standing in for ``InvocationContext``."""

    ctx                  = MagicMock()
    ctx.invocation_id    = "inv-test"
    ctx.session          = MagicMock()
    ctx.session.state    = {}
    return ctx


async def _drain(wrapper: RetryingAgentWrapper, ctx: Any) -> list[Event]:
    """Collect every event the wrapper yields into a list."""

    out: list[Event] = []
    async for ev in wrapper._run_async_impl(ctx):
        out.append(ev)
    return out


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


def test_succeeds_first_try_forwards_all_events() -> None:
    """Inner succeeds on attempt 1 → 1 invocation, every event forwarded."""

    inner = _FakeInner(outcomes=[])

    wrapper = RetryingAgentWrapper(
        name         = "test",
        inner        = inner,
        retry_config = _fast_config(),
    )

    events = asyncio.run(_drain(wrapper, _fake_ctx()))

    assert inner.call_count == 1, "should invoke inner exactly once on success"
    assert len(events) == 1
    assert events[0].actions.state_delta == {"fake": "ok"}


# ---------------------------------------------------------------------------
# 2. Transient 429 — retry succeeds
# ---------------------------------------------------------------------------


def test_retries_on_429_then_succeeds() -> None:
    """Two 429s followed by success → 3 invocations, only success events yielded."""

    inner = _FakeInner(
        outcomes = [
            _make_client_error_429(),
            _make_client_error_429(),
            None,  # third attempt succeeds
        ],
    )

    wrapper = RetryingAgentWrapper(
        name         = "test",
        inner        = inner,
        retry_config = _fast_config(max_attempts=5),
    )

    events = asyncio.run(_drain(wrapper, _fake_ctx()))

    assert inner.call_count == 3
    # Only the events from the successful attempt — partial yields from
    # the failed attempts must be discarded.
    assert len(events) == 1
    assert events[0].actions.state_delta == {"fake": "ok"}


# ---------------------------------------------------------------------------
# 3. Persistent 429 — exhausted attempts re-raise
# ---------------------------------------------------------------------------


def test_persistent_429_raises_after_max_attempts() -> None:
    """429 every attempt → exactly max_attempts invocations, then re-raise."""

    inner = _FakeInner(
        outcomes = [_make_client_error_429() for _ in range(10)],
    )

    wrapper = RetryingAgentWrapper(
        name         = "test",
        inner        = inner,
        retry_config = _fast_config(max_attempts=3),
    )

    with pytest.raises(ClientError):
        asyncio.run(_drain(wrapper, _fake_ctx()))

    assert inner.call_count == 3, "must stop after max_attempts attempts"


# ---------------------------------------------------------------------------
# 4. Non-retryable error — immediate propagation
# ---------------------------------------------------------------------------


def test_non_429_error_propagates_without_retry() -> None:
    """A ValueError must propagate immediately on the first attempt."""

    inner = _FakeInner(
        outcomes = [ValueError("not a rate limit")],
    )

    wrapper = RetryingAgentWrapper(
        name         = "test",
        inner        = inner,
        retry_config = _fast_config(max_attempts=5),
    )

    with pytest.raises(ValueError, match="not a rate limit"):
        asyncio.run(_drain(wrapper, _fake_ctx()))

    assert inner.call_count == 1, "non-retryable errors must not retry"


# ---------------------------------------------------------------------------
# 5. Detection helper — chained-cause exception is still recognised
# ---------------------------------------------------------------------------


def test_is_resource_exhausted_walks_cause_chain() -> None:
    """A RuntimeError that wraps a 429 ClientError must still be retryable."""

    inner_err  = _make_client_error_429()
    outer_err  = RuntimeError("ADK wrapper error")
    outer_err.__cause__ = inner_err

    assert _is_resource_exhausted(outer_err) is True


def test_is_resource_exhausted_rejects_non_429_client_error() -> None:
    """A ClientError with status 400 must NOT be retried."""

    err = ClientError.__new__(ClientError)
    err.status_code   = 400
    err.code          = 400
    err.message       = "Bad Request"
    err.status        = "INVALID_ARGUMENT"
    err.details       = {}
    err.response_json = {"error": {"code": 400}}
    err.args          = ("400 INVALID_ARGUMENT",)

    assert _is_resource_exhausted(err) is False
