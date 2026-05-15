"""Deterministic Social analyst — proper BaseAgent implementation.

``SocialAnalyst`` is a ``BaseAgent`` subclass (not ``LlmAgent``).  The
run-loop is split cleanly across three hooks:

1. ``social_fetch_callback`` (``before_agent_callback``) — fetches Finnhub
   Reddit + Twitter aggregates and writes ``state["social_data"]``.  Returns
   ``None`` so the agent body runs normally.

2. ``_run_async_impl`` — reads ``state["social_data"]``, runs
   ``extract_social_features`` + ``derive_social_verdict`` deterministically
   for every ticker, and writes ``state["social_verdicts"]`` directly to
   session state (same pattern as ``RiskGateAgent`` and ``MemoryWriter``).

3. ``make_evidence_callback`` (``after_agent_callback``) — converts the
   pre-seeded ``social_verdicts`` into ``AnalystEvidence`` records and writes
   them to ``state["social_evidence"]``.

This design avoids the C1 bug from the previous ``LlmAgent`` shell: the
after-callback fires unconditionally because ``_run_async_impl`` never
returns a ``Content`` that would set ``ctx.end_invocation = True``.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import SocialHeuristics, load_heuristics
from contract.extractors.social import derive_social_verdict, extract_social_features
from observability.trace import _trace_maybe

from .fetch import social_fetch_callback


class SocialAnalyst(BaseAgent):
    """Deterministic Social analyst — no LLM calls; all verdicts from heuristics.

    Reads ``state["social_data"]`` (populated by the fetch callback), runs
    ``extract_social_features`` + ``derive_social_verdict`` for each ticker,
    and writes ``state["social_verdicts"]``.  The registered
    ``after_agent_callback`` (``make_evidence_callback``) then converts those
    verdicts into ``AnalystEvidence`` records under ``state["social_evidence"]``.
    """

    # Pydantic field — SocialHeuristics is itself a frozen Pydantic model,
    # so it survives the arbitrary_types_allowed guard below.
    heuristics: SocialHeuristics

    # Required so Pydantic accepts SocialHeuristics (a frozen Pydantic model)
    # as a field value without raising "arbitrary types not allowed".
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, heuristics: SocialHeuristics, **kwargs: Any) -> None:
        """Initialise the SocialAnalyst and wire the fetch + evidence callbacks.

        Args:
            heuristics: Frozen ``SocialHeuristics`` config section loaded from
                        ``config/analyst_heuristics.json``.
            **kwargs:   Forwarded to ``BaseAgent.__init__``.
        """
        # Pass heuristics as a keyword argument so Pydantic sets the field
        # through its normal validated path.  Callbacks are wired here rather
        # than as class-level defaults so each instance gets fresh closures.
        super().__init__(
            name="SocialAnalyst",
            heuristics=heuristics,
            before_agent_callback=social_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="social",
                extractor=extract_social_features,
                verdicts_state_key="social_verdicts",
            ),
            **kwargs,
        )

    async def _run_async_impl(
        self, ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Compute per-ticker social verdicts deterministically and write to state.

        Reads ``state["social_data"]`` (written by the fetch callback), runs
        ``extract_social_features`` + ``derive_social_verdict`` for every
        ticker, and writes the resulting verdict dict to
        ``state["social_verdicts"]``.  The after-callback
        (``make_evidence_callback``) then converts those verdicts into
        ``AnalystEvidence`` records.

        Args:
            ctx: ADK invocation context providing access to session state.

        Yields:
            Nothing — state mutation is written directly, matching the pattern
            used by MemoryWriter, RiskGateAgent, and EvidenceWriter.
        """
        state = ctx.session.state
        social_data: dict[str, dict] = state.get("social_data") or {}

        # Historical clock: backtest sets state["as_of"]; live falls back to None
        # (the extractor ignores it for clock-free features).
        as_of = state.get("as_of") or None

        # Build as a list of dicts so make_evidence_callback can iterate them
        # and build its ticker → verdict lookup.  Each dict includes a
        # "ticker" key alongside the AnalystVerdict fields.
        verdicts: list[dict[str, Any]] = []

        for ticker, payload in social_data.items():
            features = extract_social_features(payload, ticker, as_of=as_of)
            verdict = derive_social_verdict(features, self.heuristics)
            v_dict = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)

        # Write the verdict list so the after_agent_callback can read it.
        state["social_verdicts"] = verdicts

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(ctx.session.state, "02_social_verdict", verdicts)

        # No events emitted — pure state mutation, same as RiskGateAgent.
        return
        yield  # required to make this an async generator


# Module-level singleton — used directly by unit tests and the analyst_pool
# singleton in agents/analysts/__init__.py.
social_analyst = SocialAnalyst(heuristics=load_heuristics().social)


def _build_social_analyst(heuristics: SocialHeuristics | None = None) -> SocialAnalyst:
    """Construct a fresh ``SocialAnalyst`` for the orchestrator factory.

    Args:
        heuristics: Optional pre-loaded ``SocialHeuristics``.  When ``None``,
                    ``load_heuristics()`` is called to obtain the cached config.

    Returns:
        A new ``SocialAnalyst`` instance bound to the given heuristics.
    """
    if heuristics is None:
        heuristics = load_heuristics().social
    return SocialAnalyst(heuristics=heuristics)
