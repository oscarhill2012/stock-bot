"""Unit tests for :class:`agents.llm_retry.RetryingAgentWrapper` and the
classification / sleep / merge helpers it relies on.

Covers (per the three-layer retry spec):

* Per-class budget independence (a timeout consumes only the timeout
  counter; not the 429 counter).
* asyncio.wait_for enforcement of the per-agent timeout.
* Event buffering — failed-attempt events are discarded; only the
  successful attempt's events flush.
* state_delta emission of the per-tick retry counter accumulator.
* StrategistContractViolation propagates immediately (not retried).
* Structured llm_retry_exhausted ERROR log on terminal exhaustion.
* Existing 429 happy-path / persistent / non-retryable behaviour
  preserved verbatim.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import MagicMock

import pytest
from google.adk.events import Event, EventActions
from google.genai.errors import ClientError
from pydantic import BaseModel as _BM, ValidationError as _VE

from agents.llm_retry import (
    RetryingAgentWrapper,
    RetryPolicy,
    build_retry_policies,
    _classify,
    _compute_exp_jitter,
    _format_schema_error_for_llm,
    _is_rate_limit,
    _is_schema_error,
    _is_timeout,
    _merge_increment,
    _sleep_per_policy,
)


# ---------------------------------------------------------------------------
# Shared fixtures and stubs
# ---------------------------------------------------------------------------


class _Tiny(_BM):
    """Trivial Pydantic model used to construct a real ValidationError."""

    name: str


def _make_validation_error() -> _VE:
    """Produce a real ``pydantic.ValidationError`` by failing a model parse."""

    try:
        _Tiny.model_validate({"name": 123})
    except _VE as ve:
        return ve

    raise AssertionError("Pydantic accepted invalid payload — test premise broken.")


def _fast_policies(
    *,
    rate_limit_attempts: int = 5,
    timeout_attempts:    int = 3,
    schema_attempts:     int = 3,
) -> dict[str, RetryPolicy]:
    """Build a policy dict with sub-second 429 backoff for fast tests."""

    return {
        "rate_limit": RetryPolicy(
            max_attempts       = rate_limit_attempts,
            backoff            = "exp_jitter",
            base_delay_seconds = 0.001,
            max_delay_seconds  = 0.005,
        ),
        "timeout":    RetryPolicy(max_attempts=timeout_attempts, backoff="immediate"),
        "schema":     RetryPolicy(max_attempts=schema_attempts,  backoff="immediate"),
    }


class _FakeInner:
    """Configurable fake of an ADK BaseAgent.

    Stores a script of per-attempt outcomes.  On each ``run_async`` call
    it advances the script: an outcome can be either an Exception
    (raised) or a list of Events (yielded).  Used by every wrapper test
    to simulate transient / persistent failures and successes.
    """

    def __init__(
        self,
        *,
        name:     str,
        script:   list[Any],          # each item: Exception | list[Event] | "sleep"
        sleep_s:  float | None = None,
    ) -> None:
        self.name        = name
        self._script     = list(script)
        self._sleep_s    = sleep_s
        self.call_count  = 0

    async def run_async(
        self, ctx: Any,
    ) -> AsyncGenerator[Event, None]:
        """Yield (or raise) per the next scripted outcome."""

        self.call_count += 1

        if not self._script:
            raise AssertionError(
                f"_FakeInner({self.name!r}) ran out of scripted outcomes "
                f"(call {self.call_count})"
            )

        outcome = self._script.pop(0)

        if isinstance(outcome, BaseException):
            raise outcome

        if outcome == "sleep":
            # Sleep longer than the wrapper's timeout so asyncio.wait_for fires.
            await asyncio.sleep(self._sleep_s)
            yield Event(author=self.name, content=None, actions=EventActions())
            return

        # Otherwise it's a list of Events to yield.
        for ev in outcome:
            yield ev


def _ctx_with_state() -> MagicMock:
    """Return a MagicMock ctx whose .session.state is a real dict.

    The wrapper reads ``ctx.session.state.get(retry_state_key)`` to
    build the incremental state_delta payload, and yields
    ``Event(state_delta=...)``.  Tests inspect the dict directly.

    We use an unspec'd MagicMock (not ``spec=InvocationContext``) so
    that ``ctx.session.state = {}`` is permitted without triggering the
    MagicMock attribute-restriction machinery.
    """

    ctx = MagicMock()
    ctx.session.state = {}
    return ctx


# ---------------------------------------------------------------------------
# Classification predicates and dispatcher
# ---------------------------------------------------------------------------


def test_is_rate_limit_recognises_429_client_error() -> None:
    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    assert _is_rate_limit(err) is True
    assert _classify(err)      == "rate_limit"


def test_is_rate_limit_walks_cause_chain() -> None:
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
    assert _is_timeout(asyncio.TimeoutError()) is True
    assert _is_timeout(TimeoutError())          is True
    assert _classify(asyncio.TimeoutError())    == "timeout"


def test_is_schema_error_recognises_pydantic_validation_error() -> None:
    ve = _make_validation_error()

    assert _is_schema_error(ve) is True
    assert _classify(ve)        == "schema"


def test_is_schema_error_walks_cause_chain() -> None:
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
    assert _classify(ValueError("nope")) is None


def test_classify_returns_none_for_strategist_contract_violation() -> None:
    from agents.strategist.derivation import StrategistContractViolation

    assert _classify(StrategistContractViolation("off-watchlist")) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_retry_policy_immediate_defaults_delays_to_zero() -> None:
    p = RetryPolicy(max_attempts=3, backoff="immediate")
    assert p.base_delay_seconds == 0.0
    assert p.max_delay_seconds  == 0.0


def test_compute_exp_jitter_grows_with_attempt_number() -> None:
    delays = [
        _compute_exp_jitter(attempt_n=n, base=2.0, max_=30.0)
        for n in range(1, 6)
    ]
    assert all(d >= 2.0  for d in delays)
    assert all(d <= 30.0 for d in delays)
    assert delays[-1] >= 10.0


@pytest.mark.asyncio
async def test_sleep_per_policy_immediate_does_not_sleep(monkeypatch) -> None:
    sleeps: list[float] = []

    async def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    p = RetryPolicy(max_attempts=3, backoff="immediate")
    await _sleep_per_policy(p, attempt_n=1)

    assert sleeps == [] or sleeps == [0.0]


@pytest.mark.asyncio
async def test_sleep_per_policy_exp_jitter_sleeps_within_bounds(monkeypatch) -> None:
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


def test_merge_increment_returns_new_dict_and_increments() -> None:
    current = {"rate_limit": 1}
    out     = _merge_increment(current, "timeout")
    assert current == {"rate_limit": 1}
    assert out     == {"rate_limit": 1, "timeout": 1}

    out2 = _merge_increment({"schema": 2}, "schema")
    assert out2 == {"schema": 3}


def test_build_retry_policies_composes_three_classes(monkeypatch) -> None:
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
    assert policies["rate_limit"].max_attempts == 5
    assert policies["rate_limit"].backoff      == "exp_jitter"
    assert policies["timeout"].max_attempts    == 3
    assert policies["timeout"].backoff         == "immediate"
    assert policies["schema"].max_attempts     == 3
    assert policies["schema"].backoff          == "immediate"


# ---------------------------------------------------------------------------
# Wrapper happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_succeeds_first_try_forwards_all_events() -> None:
    """Inner succeeds on first call; every event is yielded in order."""

    ev1 = Event(author="X", content=None, actions=EventActions())
    ev2 = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[[ev1, ev2]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    ctx = _ctx_with_state()

    out: list[Event] = []
    async for ev in wrapper._run_async_impl(ctx):
        out.append(ev)

    assert inner.call_count == 1
    assert out == [ev1, ev2]


# ---------------------------------------------------------------------------
# Per-class retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_retries_up_to_max_then_raises() -> None:
    """Six consecutive 429s exhaust max_attempts=5 and re-raise."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    inner = _FakeInner(name="X", script=[err] * 6)

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(rate_limit_attempts=5),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ClientError):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    assert inner.call_count == 5


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds() -> None:
    """Two 429s followed by success yields only the success events."""

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    ev  = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[err, err, [ev]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        out.append(e)

    assert inner.call_count == 3
    # The two retry state_delta events come first; the success event last.
    assert out[-1] is ev


@pytest.mark.asyncio
async def test_timeout_retries_up_to_max_then_raises() -> None:
    """Four consecutive timeouts exhaust max_attempts=3 and re-raise TimeoutError."""

    inner = _FakeInner(
        name    = "X",
        script  = ["sleep"] * 4,
        sleep_s = 1.0,                                  # longer than wrapper timeout
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 0.05,                         # 50ms — easy to overshoot
        policies        = _fast_policies(timeout_attempts=3),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(TimeoutError):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    assert inner.call_count == 3


@pytest.mark.asyncio
async def test_schema_retries_up_to_max_then_raises() -> None:
    """Four ValidationErrors exhaust max_attempts=3 and re-raise."""

    inner = _FakeInner(
        name   = "X",
        script = [_make_validation_error() for _ in range(4)],
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(schema_attempts=3),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(_VE):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    assert inner.call_count == 3


@pytest.mark.asyncio
async def test_independent_budgets_per_class() -> None:
    """One 429 + one timeout + one schema + success — none of those budgets
    individually exhaust, so all four attempts run and the success yields."""

    err_429 = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )
    ev      = Event(author="X", content=None, actions=EventActions())

    # Script: 429 → timeout (sleep) → schema → success.
    inner = _FakeInner(
        name    = "X",
        script  = [err_429, "sleep", _make_validation_error(), [ev]],
        sleep_s = 1.0,
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 0.05,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        out.append(e)

    assert inner.call_count == 4
    assert out[-1] is ev


@pytest.mark.asyncio
async def test_unclassified_exception_propagates_immediately() -> None:
    """A ValueError is unclassified — wrapper raises on the first attempt."""

    inner = _FakeInner(name="X", script=[ValueError("boom")])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ValueError, match="boom"):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    assert inner.call_count == 1


@pytest.mark.asyncio
async def test_strategist_contract_violation_not_retried() -> None:
    """StrategistContractViolation propagates immediately (no retry)."""

    from agents.strategist.derivation import StrategistContractViolation

    inner = _FakeInner(
        name   = "Strategist",
        script = [StrategistContractViolation("off-watchlist")],
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_strategist_retries",
    )

    with pytest.raises(StrategistContractViolation):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    assert inner.call_count == 1


# ---------------------------------------------------------------------------
# Event buffering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_buffer_discards_failed_attempt_events() -> None:
    """A failed attempt's yielded events do not reach the outer pipeline."""

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )

    e_fail = Event(author="X", content=None, actions=EventActions())
    e_succ = Event(author="X", content=None, actions=EventActions())

    # Custom fake that yields one event THEN raises on the first call,
    # then yields a different event and succeeds on the second.
    class _PartialFail:
        name = "X"
        call_count = 0

        async def run_async(self, ctx):                # type: ignore[no-untyped-def]
            _PartialFail.call_count += 1

            if _PartialFail.call_count == 1:
                yield e_fail
                raise err

            yield e_succ

    wrapper = RetryingAgentWrapper(
        inner           = _PartialFail(),
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    out: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        out.append(e)

    # The failed-attempt event e_fail must NOT appear in the inner-event stream.
    # The success event e_succ must appear (after the retry's state_delta event).
    assert e_fail not in out
    assert e_succ in out


# ---------------------------------------------------------------------------
# Per-tick retry-counter telemetry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_emits_state_delta_event_for_obs_counter() -> None:
    """After a retry, the wrapper has yielded a state_delta event with
    the retry-counter increment BEFORE the inner's success events."""

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )
    ev  = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[err, [ev]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_news_retries",
    )

    ctx = _ctx_with_state()

    out: list[Event] = []
    async for e in wrapper._run_async_impl(ctx):
        out.append(e)

    # The first event must be the state_delta increment for rate_limit;
    # the success event ev must come after.
    delta_evs = [
        e for e in out
        if e.actions is not None
        and e.actions.state_delta
        and "temp:_obs_news_retries" in (e.actions.state_delta or {})
    ]

    assert len(delta_evs) == 1
    delta = delta_evs[0].actions.state_delta["temp:_obs_news_retries"]
    assert delta == {"rate_limit": 1}

    # And the increment event comes before the success event in the stream.
    assert out.index(delta_evs[0]) < out.index(ev)


# ---------------------------------------------------------------------------
# Exhaustion log
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exhaustion_emits_structured_error_log(caplog) -> None:
    """On terminal exhaustion, exactly one llm_retry_exhausted ERROR row appears."""

    import logging

    caplog.set_level(logging.ERROR, logger="agents.llm_retry")

    err = ClientError(
        code          = 429,
        response_json = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response      = MagicMock(),
    )

    inner = _FakeInner(name="X", script=[err] * 6)

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(rate_limit_attempts=5),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ClientError):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    # Filter by the structured ``kind`` extra rather than the formatted message
    # string — the message string now folds in the exception detail (see
    # llm_retry.py change for why) and is no longer the bare event slug.
    exhausted = [r for r in caplog.records if getattr(r, "kind", None) == "llm_retry_exhausted"]
    assert len(exhausted) == 1
    rec = exhausted[0]
    assert rec.exhausted_class == "rate_limit"
    assert rec.attempts_used   == {"rate_limit": 5, "timeout": 0, "schema": 0}


# ---------------------------------------------------------------------------
# Agent-name attribution on retry log records
#
# Folded in from the deleted ``tests/agents/test_llm_retry_agent_name.py``
# (which pinned the legacy tenacity ``before_sleep`` hook).  The contract
# survives the rewrite: every retry-class log record must carry the
# wrapped agent's name in ``extra["agent"]`` so log analysis can attribute
# retries without an adjacent-row heuristic.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_attempt_log_carries_inner_agent_name(caplog) -> None:
    """One 429 + success → the WARNING record's ``agent`` extra equals inner.name."""

    import logging

    caplog.set_level(logging.WARNING, logger="agents.llm_retry")

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    ev  = Event(author="TestAnalyst", content=None, actions=EventActions())

    # _FakeInner.name defaults — override via constructor to a recognisable string.
    inner = _FakeInner(name="TestAnalyst", script=[err, [ev]])

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(),
        retry_state_key = "temp:_obs_test_retries",
    )

    async for _ in wrapper._run_async_impl(_ctx_with_state()):
        pass

    # See note on the exhausted-filter test above — gate on the structured
    # ``kind`` extra, not the (now exception-decorated) message string.
    attempts = [r for r in caplog.records if getattr(r, "kind", None) == "llm_retry_attempt"]
    assert len(attempts) == 1
    assert attempts[0].agent       == "TestAnalyst"
    assert attempts[0].retry_class == "rate_limit"


@pytest.mark.asyncio
async def test_retry_exhausted_log_carries_inner_agent_name(caplog) -> None:
    """Terminal exhaustion's ERROR record likewise carries ``agent`` = inner.name."""

    import logging

    caplog.set_level(logging.ERROR, logger="agents.llm_retry")

    err = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )

    inner = _FakeInner(name="TestAnalyst", script=[err] * 6)

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(rate_limit_attempts=5),
        retry_state_key = "temp:_obs_test_retries",
    )

    with pytest.raises(ClientError):
        async for _ in wrapper._run_async_impl(_ctx_with_state()):
            pass

    # See note on the exhausted-filter test above — gate on the structured
    # ``kind`` extra, not the (now exception-decorated) message string.
    exhausted = [r for r in caplog.records if getattr(r, "kind", None) == "llm_retry_exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0].agent == "TestAnalyst"


# ---------------------------------------------------------------------------
# Schema-error feedback path
# ---------------------------------------------------------------------------


def test_format_schema_error_extracts_msg_and_strips_pydantic_prefix() -> None:
    """``_format_schema_error_for_llm`` should render the validator's
    human-readable ``msg`` text and strip Pydantic's ``Value error, ``
    discriminator so the LLM sees just the actionable rule.
    """

    ve = _make_validation_error()
    rendered = _format_schema_error_for_llm(ve)

    # Header and the underlying msg are present.  The header was strengthened
    # from "Schema validator feedback" to a hard "CORRECTION REQUIRED" directive
    # when the placeholder moved to the top of the strategist prompt — the
    # imperative tone steers stubborn retries that ignored the gentler wording.
    assert "CORRECTION REQUIRED" in rendered
    assert "Input should be a valid string" in rendered

    # The Pydantic-internal ``[type=...]`` discriminator and the docs URL
    # must NOT leak into the prompt (they are noise for the model).
    assert "pydantic.dev" not in rendered
    assert "[type=" not in rendered


def test_format_schema_error_walks_cause_chain() -> None:
    """A ValidationError wrapped via ``raise X from ve`` must still
    produce a feedback message — the wrapper relies on this to feed
    LLM-output-validation failures back even when ADK has re-raised
    them as a different exception type.
    """

    ve = _make_validation_error()

    try:
        raise RuntimeError("wrapper") from ve
    except RuntimeError as outer:
        rendered = _format_schema_error_for_llm(outer)

    assert "Input should be a valid string" in rendered


@pytest.mark.asyncio
async def test_schema_retry_writes_feedback_to_state_key_when_configured() -> None:
    """When ``schema_error_state_key`` is configured, the wrapper must
    yield a ``state_delta`` event populating that key with formatted
    validator feedback BEFORE the next attempt — turning schema retries
    from blind rerolls into actual error-correction loops.
    """

    ev_success = Event(author="X", content=None, actions=EventActions())

    # Script: one schema error → success.  Need budgets ≥ 2 so the
    # schema retry actually proceeds (single attempt would exhaust).
    inner = _FakeInner(
        name   = "X",
        script = [_make_validation_error(), [ev_success]],
    )

    wrapper = RetryingAgentWrapper(
        inner                  = inner,
        timeout_seconds        = 5.0,
        policies               = _fast_policies(schema_attempts=3),
        retry_state_key        = "temp:_obs_test_retries",
        schema_error_state_key = "temp:_last_schema_error",
    )

    events: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        events.append(e)

    # Locate the feedback event by inspecting state_delta keys.
    feedback_events = [
        e for e in events
        if e.actions is not None
        and e.actions.state_delta is not None
        and "temp:_last_schema_error" in e.actions.state_delta
    ]

    assert len(feedback_events) == 1, (
        f"expected exactly one feedback state_delta event; got {len(feedback_events)}"
    )

    payload = feedback_events[0].actions.state_delta["temp:_last_schema_error"]
    assert isinstance(payload, str)
    assert "CORRECTION REQUIRED" in payload
    # The successful retry's event must still flush to the outer pipeline.
    assert ev_success in events


@pytest.mark.asyncio
async def test_schema_retry_omits_feedback_when_key_not_configured() -> None:
    """Default behaviour (no ``schema_error_state_key``) must preserve
    the legacy "reroll the dice" retry — no extra state_delta events,
    no contract change for callers that have not opted in.
    """

    ev_success = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(
        name   = "X",
        script = [_make_validation_error(), [ev_success]],
    )

    wrapper = RetryingAgentWrapper(
        inner           = inner,
        timeout_seconds = 5.0,
        policies        = _fast_policies(schema_attempts=3),
        retry_state_key = "temp:_obs_test_retries",
        # schema_error_state_key intentionally omitted — default None.
    )

    events: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        events.append(e)

    # No event should carry a "temp:_last_schema_error" state_delta key
    # because the wrapper was not asked to write one.
    for e in events:
        delta = (e.actions.state_delta or {}) if e.actions is not None else {}
        assert "temp:_last_schema_error" not in delta


@pytest.mark.asyncio
async def test_rate_limit_retry_does_not_emit_schema_feedback() -> None:
    """Schema-feedback emission is scoped to the schema retry class —
    a rate-limit retry must not write to the schema-error state key
    (a 429 is not the model's fault).
    """

    err_429 = ClientError(
        code            = 429,
        response_json   = {"error": {"status": "RESOURCE_EXHAUSTED"}},
        response        = MagicMock(),
    )
    ev_success = Event(author="X", content=None, actions=EventActions())

    inner = _FakeInner(name="X", script=[err_429, [ev_success]])

    wrapper = RetryingAgentWrapper(
        inner                  = inner,
        timeout_seconds        = 5.0,
        policies               = _fast_policies(rate_limit_attempts=3),
        retry_state_key        = "temp:_obs_test_retries",
        schema_error_state_key = "temp:_last_schema_error",
    )

    events: list[Event] = []
    async for e in wrapper._run_async_impl(_ctx_with_state()):
        events.append(e)

    for e in events:
        delta = (e.actions.state_delta or {}) if e.actions is not None else {}
        assert "temp:_last_schema_error" not in delta
