"""YieldingAnalystWrapper — BaseAgent that proxies an inner agent and emits
the inner's evidence write as a yielded ``state_delta``.

Used to convert the existing Fundamental and News LlmAgent's after-callback
direct-mutation evidence write into a Rule-1-conformant
``Event(actions=EventActions(state_delta=…))`` yield.

The wrapper:
1. Delegates to the inner agent (an ``LlmAgent`` plus its callbacks).  All
   intermediate events from the inner agent are forwarded unchanged.
2. After the inner agent returns, reads the evidence list from
   ``ctx.session.state[evidence_state_key]`` and yields one new event whose
   ``state_delta`` carries that list under the same key.

The result: even though the inner LlmAgent's after_agent_callback wrote
directly to state (it has to — ADK callbacks cannot yield events), the
outer wrapper republishes the write as a proper ``state_delta`` so ADK's
``SessionService.append_event`` persists it.  The inner direct write
becomes redundant with the outer yield — kept defensively for one cycle
so that consumers in the same invocation continue to see the value
without waiting for the event flush.

A future cleanup can drop the inner direct mutation once the persistence
layer is wired and all session backends honour ``state_delta`` writes
identically.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions


class YieldingAnalystWrapper(BaseAgent):
    """Proxy an inner agent and republish its evidence write as a ``state_delta``.

    Attributes:
        inner: The wrapped agent (typically an ``LlmAgent``).  Run via its
            ``run_async`` async-generator interface; all events it yields are
            passed through to the outer pipeline.
        evidence_state_key: The state key the inner agent's after-callback
            writes to (e.g. ``"fundamental_evidence"``).  The wrapper reads
            this after the inner agent has returned and republishes the
            value on a ``state_delta`` event.
    """

    # Declared as Pydantic fields and passed through super().__init__() so
    # Pydantic's BaseModel validates them normally.  ``arbitrary_types_allowed``
    # is required because ``inner`` is typically an ADK ``LlmAgent`` (not a
    # Pydantic model) — the same pattern used by ``SocialAnalyst`` for its
    # ``heuristics`` field.
    inner: Any
    evidence_state_key: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name: str,
        inner: Any,
        evidence_state_key: str,
    ) -> None:
        """Initialise the wrapper.

        Args:
            name: ADK agent name (e.g. ``"FundamentalAnalystBranch"``).
            inner: The inner agent instance (an ADK ``LlmAgent`` or any
                object that exposes an ``async def run_async(ctx)``
                yielding ``Event`` instances).
            evidence_state_key: State key the inner writes its evidence
                list into.
        """
        # Pass all fields through super().__init__() so Pydantic sets them via
        # its normal validated path — the same pattern used by SocialAnalyst.
        super().__init__(
            name=name,
            inner=inner,
            evidence_state_key=evidence_state_key,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Delegate to ``inner``; after it completes, republish the evidence write.

        Args:
            ctx: ADK invocation context.

        Yields:
            Every event yielded by the inner agent, then one additional
            event carrying ``state[self.evidence_state_key]`` on its
            ``state_delta``.
        """
        # 1. Pass through every event the inner agent yields.
        async for inner_event in self.inner.run_async(ctx):
            yield inner_event

        # 2. The inner agent's after_agent_callback has by now written the
        # evidence list to ``state[self.evidence_state_key]``.  Republish
        # it as a yielded state_delta so the write becomes durable.
        evidence_payload = ctx.session.state.get(self.evidence_state_key)
        if evidence_payload is not None:
            yield Event(
                author        = self.name,
                invocation_id = ctx.invocation_id,
                actions       = EventActions(state_delta={
                    self.evidence_state_key: evidence_payload,
                }),
            )
