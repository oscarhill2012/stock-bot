"""RetryingAgentWrapper — wrap any ADK agent with three-class retry
(rate-limit / timeout / schema) plus a per-call wall-clock timeout.

Why this exists
---------------
Vertex AI's Gemini models share capacity via Dynamic Shared Quota by
default — transient HTTP 429 responses are a normal operating condition,
not a true outage.  Beyond 429s, two other failure modes are observable
in practice:

* **Wall-clock runaways.**  A model that streams forever (or hangs in a
  callback) blocks the tick indefinitely if no timeout is applied.
* **Schema-validation failures.**  ADK validates each LLM output against
  the agent's ``output_schema``; a ``pydantic.ValidationError`` on
  mismatch used to propagate straight out with no retry.

This wrapper handles all three failure classes with independent per-class
attempt budgets.

What this wrapper must NOT wrap
-------------------------------
Because events are buffered until the inner completes, the wrapper
**breaks any inter-child state propagation inside a composite inner**
— e.g. ``SequentialAgent[ContextShim, LlmAgent]`` where ContextShim
writes ``state_delta`` that the LlmAgent reads via ADK's
instruction-template substitution.  Buffering means the ADK Runner
never sees ContextShim's event during the inner run, never applies the
``state_delta`` to ``ctx.session.state``, and the LlmAgent's
``inject_session_state`` raises ``KeyError: 'Context variable not
found: …'``.

Rule: only wrap units that are single LLM-calling agents (a bare
``LlmAgent``).  For the strategist, the retry wrap goes *inside* the
``SequentialAgent`` so ContextShim runs unwrapped (see
:func:`agents.strategist.agent.build_strategist`).
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncGenerator
from typing import Any, Literal

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from pydantic import BaseModel, Field

_LOGGER = logging.getLogger(__name__)


def _is_rate_limit(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or any link in its cause chain) is a
    Vertex AI HTTP 429 / RESOURCE_EXHAUSTED response.

    This is the rate-limit predicate used by :func:`_classify`.  The body
    is the same as the legacy ``_is_resource_exhausted`` — kept identical
    so existing behaviour is preserved verbatim.

    Two detection layers (matching the legacy function):

    * ADK's :class:`google.adk.models.google_llm._ResourceExhaustedError`
      — defensive import so a future rename does not silently break us.
    * The underlying :class:`google.genai.errors.ClientError` with
      ``status_code == 429`` — caught directly and via ``__cause__``.

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if this exception (or anything in its cause chain) is
        a Vertex 429; ``False`` otherwise.
    """

    # Layer 1 — ADK's wrapper class.  Defensive import.
    try:
        from google.adk.models.google_llm import _ResourceExhaustedError

        if isinstance(exc, _ResourceExhaustedError):
            return True

    except ImportError:
        pass

    # Layer 2 — the underlying SDK error.  The SDK stores the HTTP status
    # code in ``.code`` (set in ``APIError.__init__``).  Older or patched
    # instances may carry a ``status_code`` attribute instead; we check
    # both so the predicate works regardless of how the exception was
    # constructed (normal constructor *or* the ``__new__``-hack used in
    # tests for unrelated reasons).
    try:
        from google.genai.errors import ClientError

        if isinstance(exc, ClientError):
            http_code = getattr(exc, "code", None) or getattr(exc, "status_code", None)

            if http_code == 429:
                return True

    except ImportError:
        pass

    # Walk the __cause__ chain.  Stop on self-loops (defensive).
    cause = exc.__cause__

    if cause is not None and cause is not exc:
        return _is_rate_limit(cause)

    return False


def _is_timeout(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` is a wall-clock timeout the wrapper should retry.

    ``asyncio.TimeoutError`` is an alias for the built-in ``TimeoutError``
    from Python 3.11 onwards — checking the built-in covers both.  We do
    NOT classify network-layer ``httpx.TimeoutException`` here: those
    would only fire if Vertex itself raised an HTTP-layer timeout
    (rare, and a real infra error that retry will not fix).

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if ``exc`` is a ``TimeoutError`` (or alias); ``False``
        otherwise.
    """

    return isinstance(exc, TimeoutError)


def _is_schema_error(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or its cause chain) is a Pydantic
    ``ValidationError`` from the LLM output_schema parse.

    Walks ``__cause__`` so a ValidationError wrapped via
    ``raise SomethingElse from ve`` still classifies as a schema error.
    ``StrategistContractViolation`` is deliberately *not* classified — it
    is raised by the strategist's validation callback *after* the
    schema parse already succeeded, and is a systemic contract bug that
    retry will not fix.

    Parameters
    ----------
    exc:
        The exception to classify.

    Returns
    -------
    bool
        ``True`` if a ``pydantic.ValidationError`` appears anywhere in
        the cause chain.
    """

    # Defensive import — Pydantic is a hard project dependency, but we
    # mirror the import-guard style used by _is_rate_limit so the module
    # is uniformly robust.
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            return True

    except ImportError:
        return False

    cause = exc.__cause__

    if cause is not None and cause is not exc:
        return _is_schema_error(cause)

    return False


def _classify(exc: BaseException) -> str | None:
    """Top-level retry classifier — dispatches to the per-class predicates.

    Returns one of ``"rate_limit"``, ``"timeout"``, ``"schema"``, or
    ``None`` (not retryable).  Order matters when two predicates could
    in principle match the same exception — none currently overlap, but
    the order encodes priority should that ever change: rate-limit first
    (most common transient), then timeout, then schema.

    Parameters
    ----------
    exc:
        The exception raised by the inner agent.

    Returns
    -------
    str | None
        Class name to look up in the policy dict, or ``None`` if the
        wrapper should re-raise immediately.
    """

    if _is_rate_limit(exc):
        return "rate_limit"

    if _is_timeout(exc):
        return "timeout"

    if _is_schema_error(exc):
        return "schema"

    return None


class RetryPolicy(BaseModel):
    """Per-class retry policy used by :class:`RetryingAgentWrapper`.

    The wrapper holds a dict of policies keyed by class name
    (``"rate_limit"`` / ``"timeout"`` / ``"schema"``).  Each class has
    its own ``max_attempts`` budget and its own backoff schedule.

    Attributes
    ----------
    max_attempts:
        Total number of attempts for this class — one initial try plus
        retries.  ``3`` means "one try plus up to two retries".  Must be
        ``>= 1``.
    backoff:
        Either ``"immediate"`` (no sleep between retries — used for
        model-misbehaviour classes like timeout and schema) or
        ``"exp_jitter"`` (used for transient quota classes — currently
        only ``rate_limit``).
    base_delay_seconds:
        Lower bound on the per-retry sleep when ``backoff ==
        "exp_jitter"``.  Ignored otherwise.
    max_delay_seconds:
        Upper bound on the per-retry sleep when ``backoff ==
        "exp_jitter"``.  Ignored otherwise.
    """

    max_attempts:       int                              = Field(ge=1, le=20)
    backoff:            Literal["immediate", "exp_jitter"]
    base_delay_seconds: float = Field(default=0.0, ge=0.0)
    max_delay_seconds:  float = Field(default=0.0, ge=0.0)


def _compute_exp_jitter(*, attempt_n: int, base: float, max_: float) -> float:
    """Return an exponential-with-jitter delay in seconds for the n-th retry.

    Mirrors tenacity's ``wait_exponential_jitter`` behaviour without the
    dependency: delay = min(max_, base * 2^(attempt_n - 1)) + random
    jitter in [0, base).  Saturates at ``max_`` once exponential growth
    exceeds it.

    Parameters
    ----------
    attempt_n:
        1-based count of attempts already consumed for this class
        (i.e. the first retry passes ``attempt_n=1``).
    base:
        Lower-bound delay seed in seconds.
    max_:
        Upper-bound cap in seconds.

    Returns
    -------
    float
        Delay in seconds, in the range ``[base, max_]``.
    """

    # Exponential growth from the base, capped at max_.  attempt_n is
    # 1-based so the first retry sleeps near base; the second near 2*base; etc.
    grown  = min(max_, base * (2 ** max(0, attempt_n - 1)))

    # Add jitter in [0, base) so simultaneous wrappers don't lock-step.
    jitter = random.uniform(0, base)

    # Final clamp — jitter could push above max_ if max_ is close to grown.
    return min(max_, grown + jitter)


async def _sleep_per_policy(policy: RetryPolicy, *, attempt_n: int) -> None:
    """Sleep between retries according to ``policy.backoff``.

    For ``"immediate"`` policies this is a no-op (returns immediately
    without calling ``asyncio.sleep``) — used for timeout and schema
    classes where backing off does not help.  For ``"exp_jitter"`` it
    sleeps for the value returned by :func:`_compute_exp_jitter`.

    Parameters
    ----------
    policy:
        The per-class policy.
    attempt_n:
        1-based count of attempts already consumed for this class
        (passed through to ``_compute_exp_jitter``).
    """

    if policy.backoff == "immediate":
        return

    delay = _compute_exp_jitter(
        attempt_n = attempt_n,
        base      = policy.base_delay_seconds,
        max_      = policy.max_delay_seconds,
    )
    await asyncio.sleep(delay)


def _merge_increment(current: dict, cls: str) -> dict:
    """Return a new dict equal to ``current`` with ``current[cls]`` += 1.

    Pure function — does not mutate ``current``.  Used by the retry
    wrapper to build the ``state_delta`` payload for the per-tick
    retry-counter accumulator.

    Parameters
    ----------
    current:
        Current accumulator dict (may be empty / may lack ``cls``).
    cls:
        Retry-class name to increment (``"rate_limit"``, ``"timeout"``,
        ``"schema"``).

    Returns
    -------
    dict
        New dict equal to ``current`` with ``cls`` incremented by 1.
    """

    out      = dict(current)
    out[cls] = out.get(cls, 0) + 1
    return out


def build_retry_policies(
    *,
    timeout_retries: int,
    schema_retries:  int,
) -> dict[str, RetryPolicy]:
    """Compose the per-agent retry-policy dict for the wrapper.

    The 429 (``rate_limit``) policy is project-wide — loaded once from
    ``config/retry_429.json``.  The ``timeout`` and ``schema`` policies
    are per-agent, with their ``max_attempts`` supplied by the caller
    and their backoff hard-coded to ``"immediate"`` (no sleep — these
    are model-misbehaviour failures, not capacity issues).

    Parameters
    ----------
    timeout_retries:
        Total attempts the wrapper makes on wall-clock timeout
        (``asyncio.TimeoutError``).
    schema_retries:
        Total attempts the wrapper makes on
        ``pydantic.ValidationError`` from the LLM ``output_schema`` parse.

    Returns
    -------
    dict[str, RetryPolicy]
        Policies keyed by class name; passed to
        :class:`RetryingAgentWrapper`'s ``policies`` constructor arg.
    """

    # Resolve the project-wide 429 policy.  ``get_retry_429_policy()`` is
    # cached, so this is effectively free after the first call.
    from config.retry_429 import get_retry_429_policy

    cfg = get_retry_429_policy()

    return {
        "rate_limit": RetryPolicy(
            max_attempts       = cfg.max_attempts,
            backoff            = "exp_jitter",
            base_delay_seconds = cfg.base_delay_seconds,
            max_delay_seconds  = cfg.max_delay_seconds,
        ),
        "timeout":    RetryPolicy(max_attempts=timeout_retries, backoff="immediate"),
        "schema":     RetryPolicy(max_attempts=schema_retries,  backoff="immediate"),
    }


def _log_retry(
    agent_name: str,
    cls:        str,
    exc:        BaseException,
    remaining:  dict[str, int],
) -> None:
    """Emit a structured WARNING just before sleep-and-retry.

    Carries the wrapped agent's name, the retry class, the exception
    type/message, and the per-class remaining counts.  One row per
    retry attempt — log analysis can grep on ``kind="llm_retry_attempt"``
    to see the full retry trail.

    Parameters
    ----------
    agent_name:
        Name of the inner agent (e.g. ``"NewsAnalyst_AAPL"``).
    cls:
        Retry class — one of ``"rate_limit"``, ``"timeout"``, ``"schema"``.
    exc:
        The exception that triggered the retry.
    remaining:
        Per-class remaining attempts at the moment of the retry.
    """

    _LOGGER.warning(
        "llm_retry_attempt",
        extra={
            "kind":               "llm_retry_attempt",
            "agent":              agent_name,
            "retry_class":        cls,
            "exc_type":           type(exc).__name__,
            "exc_message":        str(exc),
            "remaining_attempts": dict(remaining),
        },
    )


def _log_exhausted(
    agent_name: str,
    cls:        str,
    exc:        BaseException,
    policies:   dict[str, RetryPolicy],
    remaining:  dict[str, int],
) -> None:
    """Emit a single structured ERROR row when a retry class exhausts.

    The wrapper calls this exactly once per terminal failure — the
    ``exhausted_class`` field names the class that ran out of attempts,
    and ``attempts_used`` shows how many attempts each class consumed
    during this wrapper run (useful for spotting cross-class chains
    like "timed out once, then schema-failed three times").

    Parameters
    ----------
    agent_name:
        Name of the inner agent.
    cls:
        The class that just exhausted.
    exc:
        The exception that exhausted the budget.
    policies:
        The wrapper's policies dict (used to back-compute attempts_used).
    remaining:
        Per-class remaining attempts at the moment of exhaustion.
    """

    _LOGGER.error(
        "llm_retry_exhausted",
        extra={
            "kind":            "llm_retry_exhausted",
            "agent":           agent_name,
            "exhausted_class": cls,
            "exc_type":        type(exc).__name__,
            "exc_message":     str(exc),
            "attempts_used":   {
                c: policies[c].max_attempts - r
                for c, r in remaining.items()
            },
        },
    )


class RetryingAgentWrapper(BaseAgent):
    """Proxy an inner ADK agent with three-class retry + per-call timeout.

    The wrapper recognises three retryable failure classes and applies an
    independent attempt budget to each:

    * **rate_limit** — Vertex HTTP 429 (RESOURCE_EXHAUSTED).
    * **timeout**    — ``asyncio.TimeoutError`` raised by the per-call
                       ``asyncio.wait_for`` that bounds the inner agent's
                       wall-clock time.
    * **schema**     — ``pydantic.ValidationError`` from ADK's
                       output_schema parse.

    The inner agent's events are buffered until an attempt completes
    without raising; only the successful attempt's events flush to the
    outer pipeline.  The wrapper's own retry-counter ``state_delta``
    events ARE forwarded immediately (not buffered) so downstream
    callbacks see a running total mid-tick.

    The wrapper MUST only wrap a single LLM-calling agent (a bare
    ``LlmAgent``).  Wrapping a ``SequentialAgent`` breaks
    inter-child state propagation — see the strategist factory
    docstring for the full rationale.

    Attributes
    ----------
    inner:
        The wrapped agent (typically a bare ``LlmAgent``).
    timeout_seconds:
        Per-call wall-clock timeout in seconds.  Enforced via
        ``asyncio.wait_for`` around ``inner.run_async``.
    policies:
        Per-class retry policy dict keyed by ``"rate_limit"`` /
        ``"timeout"`` / ``"schema"``.  Built via
        :func:`build_retry_policies` at factory time.
    retry_state_key:
        Session-state key the wrapper increments on every retry — used
        by ``observability.terminal_log.emit_analyst_summary`` to render
        the per-tick retry suffix on the analyst summary rows.
    """

    inner:           Any
    timeout_seconds: float
    policies:        dict[str, RetryPolicy]
    retry_state_key: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name:            str | None = None,
        inner:           Any,
        timeout_seconds: float,
        policies:        dict[str, RetryPolicy],
        retry_state_key: str,
    ) -> None:
        """Initialise the wrapper.

        Parameters
        ----------
        name:
            ADK agent name.  Defaults to ``"<inner.name>Retrying"`` so
            traces show the wrapping unambiguously.
        inner:
            The wrapped agent instance — must expose
            ``async def run_async(ctx)`` as an async generator.
        timeout_seconds:
            Per-call wall-clock timeout.  ``asyncio.wait_for(...)``
            raises ``asyncio.TimeoutError`` if the inner exceeds this.
        policies:
            Per-class retry policy dict.  Use :func:`build_retry_policies`
            to compose.
        retry_state_key:
            Session-state key for the per-tick retry-counter accumulator.
        """

        # Derive a meaningful wrapper name from the inner agent if the
        # caller omitted it — avoids anonymous ``None`` in trace dumps.
        resolved_name = name if name is not None else f"{inner.name}Retrying"

        # Pass every field through super().__init__() so Pydantic sets
        # them via its normal validated path.
        super().__init__(
            name            = resolved_name,
            inner           = inner,
            timeout_seconds = timeout_seconds,
            policies        = policies,
            retry_state_key = retry_state_key,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Drive the inner agent with per-class retry + wall-clock timeout.

        Per-attempt flow:

        1. Reset the events buffer.
        2. Drive the inner inside ``asyncio.wait_for(timeout_seconds)``.
           On success, break out and flush the buffer.
        3. On exception, classify; if unclassified, re-raise immediately.
        4. Decrement the matching class's remaining counter.
        5. Yield a ``state_delta`` event incrementing the retry-state-key
           accumulator (so terminal-log callbacks see the running total).
        6. If the class is now exhausted, log ``llm_retry_exhausted`` and
           re-raise.
        7. Otherwise log ``llm_retry_attempt``, sleep per the policy, and
           continue.

        Parameters
        ----------
        ctx:
            ADK invocation context.

        Yields
        ------
        Event
            The wrapper's own ``state_delta`` events (one per retry) and
            then, on success, every event from the successful attempt.
        """

        # Per-attempt event buffer — rebound at the start of every attempt
        # so a failed attempt's events are discarded before the retry.
        events: list[Event] = []

        # Per-class attempt counters — decremented when that class fires.
        # Exhaustion of any one class terminates the loop.
        remaining = {cls: pol.max_attempts for cls, pol in self.policies.items()}

        while True:
            events = []

            try:
                # Inner driver packaged as a closure so asyncio.wait_for
                # has something cancellable.  We cannot put ``yield``
                # directly inside wait_for, so we collect into the events
                # buffer and flush after the loop terminates with success.
                # ``buf`` is passed explicitly so the closure binds the
                # *current* list object rather than the loop variable
                # (avoids ruff B023).
                async def _drive(buf: list[Event] = events) -> None:
                    async for ev in self.inner.run_async(ctx):
                        buf.append(ev)

                await asyncio.wait_for(_drive(), timeout=self.timeout_seconds)

                # Success — exit the retry loop.
                break

            except BaseException as exc:
                cls = _classify(exc)

                if cls is None:
                    # Unclassified — re-raise immediately.  The
                    # IsolatedFailureWrapper (analysts) or backtest driver
                    # (strategist) handles it from here.
                    raise

                remaining[cls] -= 1

                # Emit the per-tick retry-counter state_delta BEFORE
                # checking exhaustion so the terminal-log row reflects
                # this attempt even when the next decision is to raise.
                current = ctx.session.state.get(self.retry_state_key) or {}
                yield Event(
                    author  = self.name,
                    content = None,
                    actions = EventActions(
                        state_delta = {
                            self.retry_state_key: _merge_increment(current, cls),
                        },
                    ),
                )

                if remaining[cls] <= 0:
                    _log_exhausted(self.inner.name, cls, exc, self.policies, remaining)
                    raise

                _log_retry(self.inner.name, cls, exc, remaining)

                # attempts_consumed_for_class — feeds exp-jitter for the
                # 429 path so the backoff grows attempt-by-attempt.
                # No-op for "immediate" policies.
                attempts_consumed = self.policies[cls].max_attempts - remaining[cls]
                await _sleep_per_policy(self.policies[cls], attempt_n=attempts_consumed)

                continue

        # Reached only on a successful attempt — flush buffered inner
        # events in original order.
        for ev in events:
            yield ev
