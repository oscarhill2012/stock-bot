"""RetryingAgentWrapper — wrap any ADK agent in an exponential-backoff retry
loop for Vertex AI HTTP 429 (RESOURCE_EXHAUSTED) responses.

Why this exists
---------------
Vertex AI's Gemini models share capacity via Dynamic Shared Quota by
default — transient HTTP 429 responses are a normal operating condition,
not a true outage.  Google's own guidance is that the *client* implements
exponential backoff and re-tries the call; the ADK runtime does not do
this for us, and the underlying ``google.genai`` SDK's tenacity wrapper
explicitly excludes 429 from its own retry policy.

This wrapper bridges the gap.  It composes with the existing
:class:`agents.analysts._base_yield.YieldingAnalystWrapper` (the retry
wrapper sits *outside* the yield wrapper) and applies equally well to the
strategist's bare ``LlmAgent``.  See
``docs/superpowers/specs/`` if/when a dedicated spec lands; the design
rationale lives in commit history alongside this file's introduction.

How it works
------------
1. The wrapper buffers every event that the inner agent yields.  Nothing
   is forwarded to the outer pipeline until the inner has run to
   completion *without* raising — so a 429 mid-run cannot cause a
   partial ``state_delta`` to land twice.
2. On a recognised "resource exhausted" exception, tenacity sleeps for
   an exponential-with-jitter delay (bounded by
   ``config/llm_retry.json``) and re-invokes ``inner.run_async(ctx)``.
3. Non-429 exceptions propagate immediately — these are real errors that
   the abort-ratio logic in :class:`backtest.driver.BacktestDriver`
   should see.

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

Rule: only wrap units that are single LLM-calling agents (an
``LlmAgent`` or an ``LlmAgent``-wrapping ``YieldingAnalystWrapper``).
For the strategist, the retry wrap goes *inside* the
``SequentialAgent`` so ContextShim runs unwrapped (see
:func:`agents.strategist.agent.build_strategist`).

The retry policy is read from ``config/llm_retry.json`` via
:func:`config.llm_retry.get_retry_config`.  Tests may inject a custom
:class:`config.llm_retry.RetryConfig` instance via the constructor's
``retry_config`` argument so they can run with sub-second delays.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from tenacity import (
    AsyncRetrying,
    before_sleep_log,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential_jitter,
)

from config.llm_retry import RetryConfig, get_retry_config

_LOGGER = logging.getLogger(__name__)


def _is_resource_exhausted(exc: BaseException) -> bool:
    """Return ``True`` if ``exc`` (or any link in its cause chain) is a
    Vertex AI HTTP 429 / RESOURCE_EXHAUSTED response.

    Two layers of detection are checked:

    * ADK's :class:`google.adk.models.google_llm._ResourceExhaustedError`
      — this is the immediate exception class raised by ADK when the
      underlying SDK responds with 429.  It is underscore-prefixed and
      therefore technically private; we import defensively so a future
      ADK rename does not silently break detection.
    * The underlying :class:`google.genai.errors.ClientError` with
      ``status_code == 429`` — caught both directly and via ``__cause__``
      so the wrapper still recognises the error if ADK stops wrapping it.

    Parameters
    ----------
    exc:
        The exception raised by ``inner.run_async``.

    Returns
    -------
    bool
        ``True`` if the wrapper should retry; ``False`` if the exception
        should propagate immediately.
    """

    # Layer 1 — ADK's wrapper class.  Defensive import: if a future ADK
    # version renames or removes this class, we silently fall through to
    # the SDK-level check below rather than blowing up at module import.
    try:
        from google.adk.models.google_llm import _ResourceExhaustedError

        if isinstance(exc, _ResourceExhaustedError):
            return True

    except ImportError:
        pass

    # Layer 2 — the underlying SDK error.  ``ClientError`` carries the
    # HTTP status code in its ``status_code`` attribute; only 429 should
    # be retried.  Other 4xx responses (400 bad request, 403 forbidden,
    # 404 not found) are real errors that retrying cannot fix.
    try:
        from google.genai.errors import ClientError

        if isinstance(exc, ClientError) and getattr(exc, "status_code", None) == 429:
            return True

    except ImportError:
        pass

    # Walk the ``__cause__`` chain — ADK raises its wrapper with
    # ``from ce``, so a tenacity catch on the outer exception still
    # surfaces the SDK-level 429 underneath.  Recurse defensively, but
    # stop if the chain self-loops (defensive — should never happen).
    cause = exc.__cause__

    if cause is not None and cause is not exc:
        return _is_resource_exhausted(cause)

    return False


class RetryingAgentWrapper(BaseAgent):
    """Proxy an inner ADK agent, retrying on Vertex 429 with backoff + jitter.

    The wrapper subclasses :class:`google.adk.agents.BaseAgent` so it can
    be slotted into any ADK pipeline at the same level as the agent it
    wraps.  It composes cleanly with
    :class:`agents.analysts._base_yield.YieldingAnalystWrapper` — wrap
    the yield wrapper, not the bare ``LlmAgent``, so the analyst's
    state-publishing semantics remain intact on success.

    Attributes
    ----------
    inner:
        The wrapped agent — any object exposing the
        ``async def run_async(ctx)`` async-generator interface.
    retry_config:
        Settings controlling ``max_attempts``, ``base_delay_seconds``,
        and ``max_delay_seconds``.  Defaults to the singleton loaded
        from ``config/llm_retry.json``.
    """

    # Pydantic field declarations — mirror the
    # ``YieldingAnalystWrapper`` pattern.  ``arbitrary_types_allowed``
    # is required because ``inner`` is typically an ADK agent (not a
    # Pydantic model) and ``retry_config`` is our own Pydantic model
    # which is fine on its own but lives alongside the arbitrary
    # ``inner``.
    inner:        Any
    retry_config: RetryConfig

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name:         str,
        inner:        Any,
        retry_config: RetryConfig | None = None,
    ) -> None:
        """Initialise the wrapper.

        Parameters
        ----------
        name:
            ADK agent name — surfaced in traces and event metadata.
            Conventional pattern is ``"<InnerName>Retrying"`` so the
            wrapping is obvious in a trace dump.
        inner:
            The inner agent instance.
        retry_config:
            Optional override.  Production callers pass ``None`` (or
            omit) to use :func:`config.llm_retry.get_retry_config`;
            tests pass a hand-built :class:`RetryConfig` with tiny
            delays so the test suite stays fast.
        """

        # Resolve the config eagerly rather than on every retry — saves
        # one disk read per attempt and matches the
        # ``get_models_config()`` pattern used elsewhere.
        cfg = retry_config if retry_config is not None else get_retry_config()

        # Pass every field through super().__init__() so Pydantic sets
        # them via its normal validated path — same convention as
        # YieldingAnalystWrapper.
        super().__init__(
            name         = name,
            inner        = inner,
            retry_config = cfg,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Drive the inner agent, retrying on 429 with exponential backoff.

        Events from the inner agent are buffered in-memory and only
        forwarded to the outer pipeline once the inner has completed
        without raising.  This guarantees that any ``state_delta``
        yielded by the inner is applied exactly once even across retry
        attempts.

        See the module docstring's "What this wrapper must NOT wrap"
        section: buffering breaks inter-child state propagation inside
        a composite inner (e.g. ``SequentialAgent[ContextShim,
        LlmAgent]``).  Only wrap a single LLM-calling agent.

        Parameters
        ----------
        ctx:
            ADK invocation context — passed through to the inner agent
            on every attempt.  Shared across attempts intentionally so
            the session state and invocation_id remain stable.

        Yields
        ------
        Event
            Every event the inner agent yielded on its final, successful
            attempt — in original order.

        Raises
        ------
        BaseException
            Any non-429 exception is re-raised immediately on the first
            attempt.  A 429 that persists past ``max_attempts`` is
            re-raised after the final attempt; tenacity's ``reraise=True``
            preserves the original traceback rather than wrapping in
            ``RetryError``.
        """

        # The events list is rebound at the start of every attempt so
        # partial yields from a failed attempt are discarded before the
        # retry.  Declared at function scope so it survives the
        # ``async for attempt`` loop and is visible to the yield loop
        # below.
        events: list[Event] = []

        async for attempt in AsyncRetrying(
            stop        = stop_after_attempt(self.retry_config.max_attempts),
            wait        = wait_exponential_jitter(
                initial = self.retry_config.base_delay_seconds,
                max     = self.retry_config.max_delay_seconds,
            ),
            retry       = retry_if_exception(_is_resource_exhausted),
            before_sleep = before_sleep_log(_LOGGER, logging.WARNING),
            reraise     = True,
        ):
            with attempt:

                # Drop any events buffered by a previous (failed)
                # attempt before re-running the inner agent.
                events = []

                async for ev in self.inner.run_async(ctx):
                    events.append(ev)

        # Reached only when the loop completes without re-raising —
        # i.e. one attempt succeeded.  Forward the buffered events in
        # original order so downstream agents observe them exactly as
        # the inner agent intended.
        for ev in events:
            yield ev
