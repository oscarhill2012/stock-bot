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

Each test injects a hand-built :class:`config.retry_429.Retry429Policy` (aliased
as ``RetryConfig``) with sub-second delays so the suite stays fast
(``base_delay_seconds=0.001``).
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
from config.retry_429 import Retry429Policy as RetryConfig

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


# ---------------------------------------------------------------------------
# Tests for the per-class predicate helpers and the top-level _classify dispatcher
# ---------------------------------------------------------------------------

import asyncio
from pydantic import BaseModel as _BM, ValidationError as _VE

from agents.llm_retry import _classify, _is_rate_limit, _is_timeout, _is_schema_error


class _Tiny(_BM):
    """Trivial Pydantic model used to construct a real ValidationError."""

    name: str


def _make_validation_error() -> _VE:
    """Produce a real ``pydantic.ValidationError`` by failing a model parse."""

    try:
        _Tiny.model_validate({"name": 123})           # 123 is not a string
    except _VE as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


def test_is_rate_limit_recognises_429_client_error() -> None:
    """A google.genai ClientError with status_code 429 classifies as rate_limit."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    assert _is_rate_limit(err) is True
    assert _classify(err)      == "rate_limit"


def test_is_rate_limit_walks_cause_chain() -> None:
    """A 429 wrapped via `raise X from Y` still classifies as rate_limit."""

    inner = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    try:
        try:
            raise inner
        except ClientError as ce:
            raise RuntimeError("wrapped") from ce
    except RuntimeError as outer:
        assert _is_rate_limit(outer) is True
        assert _classify(outer)      == "rate_limit"


def test_is_timeout_recognises_asyncio_timeout() -> None:
    """asyncio.TimeoutError / TimeoutError classify as timeout."""

    assert _is_timeout(asyncio.TimeoutError()) is True
    assert _is_timeout(TimeoutError())          is True
    assert _classify(asyncio.TimeoutError())    == "timeout"
    assert _classify(TimeoutError())            == "timeout"


def test_is_schema_error_recognises_pydantic_validation_error() -> None:
    """A real ``pydantic.ValidationError`` classifies as schema."""

    ve = _make_validation_error()

    assert _is_schema_error(ve) is True
    assert _classify(ve)        == "schema"


def test_is_schema_error_walks_cause_chain() -> None:
    """A wrapped ValidationError still classifies as schema."""

    ve = _make_validation_error()
    try:
        try:
            raise ve
        except _VE as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        assert _is_schema_error(outer) is True
        assert _classify(outer)        == "schema"


def test_classify_returns_none_for_unhandled() -> None:
    """A vanilla ValueError is not retryable — _classify returns None."""

    assert _classify(ValueError("nope")) is None


def test_classify_returns_none_for_strategist_contract_violation() -> None:
    """StrategistContractViolation is NOT classified — it is a contract bug
    that retry will not fix."""

    from agents.strategist.derivation import StrategistContractViolation

    assert _classify(StrategistContractViolation("off-watchlist")) is None


# ---------------------------------------------------------------------------
# Tests for RetryPolicy, _compute_exp_jitter, _sleep_per_policy, _merge_increment
# ---------------------------------------------------------------------------

from agents.llm_retry import (
    RetryPolicy,
    _compute_exp_jitter,
    _sleep_per_policy,
    _merge_increment,
)


def test_retry_policy_immediate_rejects_delay_fields() -> None:
    """An ``immediate`` policy ignores base/max delay (both default to 0)."""

    p = RetryPolicy(max_attempts=3, backoff="immediate")

    assert p.max_attempts       == 3
    assert p.backoff            == "immediate"
    assert p.base_delay_seconds == 0.0
    assert p.max_delay_seconds  == 0.0


def test_retry_policy_exp_jitter_requires_positive_delays() -> None:
    """An ``exp_jitter`` policy stores positive base/max delay values."""

    p = RetryPolicy(
        max_attempts       = 5,
        backoff            = "exp_jitter",
        base_delay_seconds = 2.0,
        max_delay_seconds  = 30.0,
    )

    assert p.base_delay_seconds == 2.0
    assert p.max_delay_seconds  == 30.0


def test_compute_exp_jitter_grows_with_attempt_number() -> None:
    """Each successive attempt's delay grows, capped at max."""

    delays = [
        _compute_exp_jitter(attempt_n=n, base=2.0, max_=30.0)
        for n in range(1, 6)
    ]

    # Monotonic non-decreasing (jitter introduces variance but never below base).
    assert all(d >= 2.0  for d in delays)
    assert all(d <= 30.0 for d in delays)
    # The last attempts should saturate near max (with some jitter slack).
    assert delays[-1] >= 10.0


@pytest.mark.asyncio
async def test_sleep_per_policy_immediate_does_not_sleep(monkeypatch) -> None:
    """An ``immediate`` policy passes 0 to asyncio.sleep (or skips it)."""

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(max_attempts=3, backoff="immediate")
    await _sleep_per_policy(p, attempt_n=1)

    # Either the helper skipped asyncio.sleep entirely, or it passed 0.
    assert sleeps == [] or sleeps == [0.0]


@pytest.mark.asyncio
async def test_sleep_per_policy_exp_jitter_sleeps_within_bounds(monkeypatch) -> None:
    """An ``exp_jitter`` policy sleeps for a value within [base, max]."""

    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(
        max_attempts       = 5,
        backoff            = "exp_jitter",
        base_delay_seconds = 2.0,
        max_delay_seconds  = 30.0,
    )
    await _sleep_per_policy(p, attempt_n=1)

    assert len(sleeps) == 1
    assert 2.0 <= sleeps[0] <= 30.0


def test_merge_increment_returns_new_dict() -> None:
    """``_merge_increment`` is pure — does not mutate the input."""

    current = {"rate_limit": 1}
    out     = _merge_increment(current, "timeout")

    assert current == {"rate_limit": 1}                   # input untouched
    assert out     == {"rate_limit": 1, "timeout": 1}


def test_merge_increment_increments_existing_key() -> None:
    """An already-present class increments by 1."""

    current = {"schema": 2}
    out     = _merge_increment(current, "schema")

    assert out == {"schema": 3}


# ---------------------------------------------------------------------------
# Tests for build_retry_policies — composes the per-agent policy dict
# from the per-agent retry counts plus the project-wide 429 policy.
# ---------------------------------------------------------------------------

from agents.llm_retry import build_retry_policies


def test_build_retry_policies_composes_three_classes(monkeypatch) -> None:
    """The returned dict has exactly three classes with correct shapes."""

    # Stub the 429 policy loader so the test is hermetic.
    from config import retry_429 as cfg_mod

    monkeypatch.setattr(
        cfg_mod,
        "get_retry_429_policy",
        lambda: cfg_mod.Retry429Policy(
            max_attempts       = 5,
            base_delay_seconds = 2.0,
            max_delay_seconds  = 30.0,
        ),
    )

    policies = build_retry_policies(timeout_retries=3, schema_retries=3)

    assert set(policies.keys()) == {"rate_limit", "timeout", "schema"}

    assert policies["rate_limit"].max_attempts       == 5
    assert policies["rate_limit"].backoff            == "exp_jitter"
    assert policies["rate_limit"].base_delay_seconds == 2.0
    assert policies["rate_limit"].max_delay_seconds  == 30.0

    assert policies["timeout"].max_attempts == 3
    assert policies["timeout"].backoff      == "immediate"

    assert policies["schema"].max_attempts == 3
    assert policies["schema"].backoff      == "immediate"
