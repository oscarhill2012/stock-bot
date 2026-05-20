"""Deterministic Technical analyst — Phase 5 BaseAgent implementation.

``TechnicalAnalyst`` is a ``BaseAgent`` subclass (not ``LlmAgent``).  The
run-loop is split cleanly across three hooks:

1. ``technical_fetch_callback`` (``before_agent_callback``) — fetches OHLCV
   price history for each ticker and writes ``state["technical_data"]``.
   Returns ``None`` so the agent body runs normally.

2. ``_run_async_impl`` — reads ``state["technical_data"]``, runs
   ``extract_technical_features`` + ``derive_technical_verdict``
   deterministically for every ticker, and yields an Event whose
   ``state_delta`` carries ``technical_verdicts``.

3. ``make_evidence_callback`` (``after_agent_callback``) — converts the
   pre-seeded ``technical_verdicts`` into ``AnalystEvidence`` records and
   writes them to ``state["technical_evidence"]``.

This design removes the LLM dependency entirely.  The old ``TECHNICAL_INSTRUCTION``
prompt module is no longer used and can be deleted.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import TechnicalHeuristics, load_heuristics
from contract.extractors.technical import derive_technical_verdict, extract_technical_features
from observability.trace import _trace_maybe

from .fetch import technical_fetch_callback


class TechnicalAnalyst(BaseAgent):
    """Deterministic Technical analyst — no LLM calls; all verdicts from heuristics.

    Reads ``state["technical_data"]`` (populated by the fetch callback), runs
    ``extract_technical_features`` + ``derive_technical_verdict`` for each
    ticker, and yields an ``Event`` whose ``state_delta`` carries
    ``technical_verdicts``.  The registered ``after_agent_callback``
    (``make_evidence_callback``) then converts those verdicts into
    ``AnalystEvidence`` records under ``state["technical_evidence"]``.
    """

    # Pydantic field — TechnicalHeuristics is itself a frozen Pydantic model,
    # so it survives the arbitrary_types_allowed guard below.
    heuristics: TechnicalHeuristics

    # Required so Pydantic accepts TechnicalHeuristics (a frozen Pydantic model)
    # as a field value without raising "arbitrary types not allowed".
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, heuristics: TechnicalHeuristics, **kwargs: Any) -> None:
        """Initialise the TechnicalAnalyst and wire the fetch + evidence callbacks.

        Args:
            heuristics: Frozen ``TechnicalHeuristics`` config section loaded from
                        ``config/analyst_heuristics.json``.
            **kwargs:   Forwarded to ``BaseAgent.__init__``.
        """
        # Pass heuristics as a keyword argument so Pydantic sets the field
        # through its normal validated path.  Callbacks are wired here rather
        # than as class-level defaults so each instance gets fresh closures.
        super().__init__(
            name="TechnicalAnalyst",
            heuristics=heuristics,
            before_agent_callback=technical_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="technical",
                extractor=extract_technical_features,
                verdicts_state_key="technical_verdicts",
            ),
            **kwargs,
        )

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Compute per-ticker technical verdicts deterministically and write to state.

        Reads ``state["technical_data"]`` (written by the fetch callback), runs
        ``extract_technical_features`` + ``derive_technical_verdict`` for every
        ticker, and writes the resulting verdict dicts to
        ``state["technical_verdicts"]``.  The after-callback
        (``make_evidence_callback``) then converts those verdicts into
        ``AnalystEvidence`` records.

        Args:
            ctx: ADK invocation context providing access to session state.

        Yields:
            One ``Event`` whose ``actions.state_delta`` carries the
            ``technical_verdicts`` list.  Conforms to contract Rule 1; no
            direct ``state[k] = v`` write is performed.
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        data: dict[str, dict] = state.get("technical_data", {}) or {}

        # Historical clock: backtest sets state["as_of"]; live falls back to None
        # (the extractor ignores it for clock-free features).
        as_of = state.get("as_of") or None

        # Build as a list of dicts so make_evidence_callback can iterate them
        # and build its ticker → verdict lookup.  Each dict includes a
        # "ticker" key alongside the AnalystVerdict fields.
        verdicts: list[dict[str, Any]] = []

        for ticker in tickers:
            # Pass the full session state so the extractor can read
            # state["reference_prices"] for relative_strength_vs_spy_* (Fix C).
            # In _run_async_impl, ctx.session.state is a plain dict, so a
            # simple dict() copy is safe here (no ADK State proxy involved).
            features = extract_technical_features(
                data.get(ticker, {}), ticker, as_of=as_of, state=dict(state),
            )
            verdict = derive_technical_verdict(features, self.heuristics)
            v_dict = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        # Run before the yield so the trace records the same payload the
        # state_delta carries.
        _trace_maybe(ctx.session.state, "02_technical_verdict", verdicts)

        # Contract Rule 1 — every state write rides on an Event whose
        # ``actions.state_delta`` carries it.  ADK's SessionService only
        # persists state via ``append_event``; a direct ``state[k] = v``
        # would be lost on any non-in-memory session backend.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={"technical_verdicts": verdicts}),
        )


# Module-level singleton — used directly by unit tests and the analyst_pool
# singleton in agents/analysts/__init__.py.
technical_analyst = TechnicalAnalyst(heuristics=load_heuristics().technical)


def _build_technical_analyst(heuristics: TechnicalHeuristics | None = None) -> TechnicalAnalyst:
    """Construct a fresh ``TechnicalAnalyst`` for the orchestrator factory.

    Args:
        heuristics: Optional pre-loaded ``TechnicalHeuristics``.  When ``None``,
                    ``load_heuristics()`` is called to obtain the cached config.

    Returns:
        A new ``TechnicalAnalyst`` instance bound to the given heuristics.
    """
    if heuristics is None:
        heuristics = load_heuristics().technical
    return TechnicalAnalyst(heuristics=heuristics)
