# src/agents/isolated_failure.py
"""IsolatedFailureWrapper — catches and logs exceptions from a child branch
without propagating them to the parent SequentialAgent.

Used by the per-ticker analyst fan-out (Phase 9): a single ticker's
persistent failure must not abort the tick.  When the inner raises, the
wrapper yields zero events; the downstream joiner then synthesises a
no-data verdict for the absent state key.

Wrapping order for per-ticker LLM branches:
    IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent))
— ``RetryingAgentWrapper`` exhausts its retries first; only then does its
exception bubble into ``IsolatedFailureWrapper``'s ``except``.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

_LOGGER = logging.getLogger(__name__)


class IsolatedFailureWrapper(BaseAgent):
    """Proxy an inner agent and suppress any exception it raises.

    The wrapper forwards every event the inner yields up to the point of
    failure.  If the inner raises, the exception is logged with structured
    fields (``analyst``, ``ticker``, ``kind="branch_failed"``, ``exc_type``,
    ``exc_message``) and *no further events* are yielded.  The wrapper
    returns normally so the parent ``SequentialAgent`` continues running.

    Attributes
    ----------
    inner:
        The wrapped agent (typically a ``RetryingAgentWrapper`` around an
        ``LlmAgent``).
    analyst:
        Short identifier of the analyst this branch belongs to (e.g.
        ``"news"`` or ``"fundamental"``).  Surfaced in the failure log.
    ticker:
        Ticker symbol this branch is bound to.  Surfaced in the failure log.
    """

    inner: Any
    analyst: str
    ticker: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        name: str,
        inner: Any,
        analyst: str,
        ticker: str,
    ) -> None:
        """Initialise the wrapper.

        Args:
            name: ADK agent name (e.g. ``"NewsAnalyst_AAPL_isolated"``).
            inner: The inner agent instance to delegate to.
            analyst: Short analyst identifier ("news" / "fundamental").
            ticker: Ticker symbol bound to this branch.
        """
        super().__init__(
            name=name,
            inner=inner,
            analyst=analyst,
            ticker=ticker,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Delegate to ``inner``; suppress and log any exception it raises.

        Args:
            ctx: ADK invocation context.

        Yields:
            Every event yielded by the inner agent up to the point of
            failure.  Yields nothing on or after an exception.
        """
        try:
            async for inner_event in self.inner.run_async(ctx):
                yield inner_event
        except Exception as exc:  # noqa: BLE001 — deliberate broad catch at the isolation boundary
            # Structured failure log — picked up by the per-tick obs/logs
            # aggregator so failed branches are visible without crashing the tick.
            _LOGGER.warning(
                "branch_failed",
                extra={
                    "kind":        "branch_failed",
                    "analyst":     self.analyst,
                    "ticker":      self.ticker,
                    "exc_type":    type(exc).__name__,
                    "exc_message": str(exc),
                },
            )
            # Deliberately return: no further events.  The downstream joiner
            # sees the missing ``temp:<analyst>_verdict_<TICKER>`` key and
            # synthesises a no-data verdict.
            return
