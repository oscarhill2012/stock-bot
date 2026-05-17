"""Deterministic SmartMoney analyst — Phase 5 BaseAgent implementation.

``SmartMoneyAnalyst`` is a ``BaseAgent`` subclass (not ``LlmAgent``).  The
run-loop is split cleanly across three hooks:

1. ``smart_money_fetch_callback`` (``before_agent_callback``) — fetches
   congressional-filing data for each ticker and writes
   ``state["smart_money_data"]``.  Returns ``None`` so the agent body runs
   normally.

2. ``_run_async_impl`` — reads ``state["smart_money_data"]``, runs
   ``extract_smart_money_features`` + ``derive_smart_money_verdict``
   deterministically for every ticker, and writes
   ``state["smart_money_verdicts"]`` directly to session state (same pattern
   as ``TechnicalAnalyst``, ``SocialAnalyst``, ``RiskGateAgent``, and
   ``MemoryWriter``).

3. ``make_evidence_callback`` (``after_agent_callback``) — converts the
   pre-seeded ``smart_money_verdicts`` into ``AnalystEvidence`` records and
   writes them to ``state["smart_money_evidence"]``.

This design removes the LLM dependency entirely.  The old
``SMART_MONEY_INSTRUCTION`` prompt module is no longer used.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from agents.analysts._common import make_evidence_callback
from agents.analysts.heuristics import SmartMoneyHeuristics, load_heuristics
from contract.extractors.smart_money import (
    derive_smart_money_verdict,
    extract_smart_money_features,
)
from observability.trace import _trace_maybe

from .fetch import smart_money_fetch_callback


class SmartMoneyAnalyst(BaseAgent):
    """Deterministic SmartMoney analyst — no LLM calls; all verdicts from heuristics.

    Reads ``state["smart_money_data"]`` (populated by the fetch callback),
    runs ``extract_smart_money_features`` + ``derive_smart_money_verdict``
    for each ticker, and writes ``state["smart_money_verdicts"]``.  The
    registered ``after_agent_callback`` (``make_evidence_callback``) then
    converts those verdicts into ``AnalystEvidence`` records under
    ``state["smart_money_evidence"]``.
    """

    # Pydantic field — SmartMoneyHeuristics is itself a frozen Pydantic model,
    # so it survives the arbitrary_types_allowed guard below.
    heuristics: SmartMoneyHeuristics

    # Required so Pydantic accepts SmartMoneyHeuristics (a frozen Pydantic model)
    # as a field value without raising "arbitrary types not allowed".
    model_config = {"arbitrary_types_allowed": True}

    def __init__(self, heuristics: SmartMoneyHeuristics, **kwargs: Any) -> None:
        """Initialise the SmartMoneyAnalyst and wire the fetch + evidence callbacks.

        Args:
            heuristics: Frozen ``SmartMoneyHeuristics`` config section loaded
                        from ``config/analyst_heuristics.json``.
            **kwargs:   Forwarded to ``BaseAgent.__init__``.
        """
        # Pass heuristics as a keyword argument so Pydantic sets the field
        # through its normal validated path.  Callbacks are wired here rather
        # than as class-level defaults so each instance gets fresh closures.
        super().__init__(
            name="SmartMoneyAnalyst",
            heuristics=heuristics,
            before_agent_callback=smart_money_fetch_callback,
            after_agent_callback=make_evidence_callback(
                analyst="smart_money",
                extractor=extract_smart_money_features,
                verdicts_state_key="smart_money_verdicts",
            ),
            **kwargs,
        )

    async def _run_async_impl(
        self,
        ctx: InvocationContext,
    ) -> AsyncGenerator[Event, None]:
        """Compute per-ticker smart-money verdicts deterministically and write to state.

        Reads ``state["smart_money_data"]`` (written by the fetch callback),
        runs ``extract_smart_money_features`` + ``derive_smart_money_verdict``
        for every ticker, and writes the resulting verdict dicts to
        ``state["smart_money_verdicts"]``.  The after-callback
        (``make_evidence_callback``) then converts those verdicts into
        ``AnalystEvidence`` records.

        Args:
            ctx: ADK invocation context providing access to session state.

        Yields:
            Nothing — state mutation is written directly, matching the pattern
            used by TechnicalAnalyst, SocialAnalyst, MemoryWriter, and
            RiskGateAgent.
        """
        state = ctx.session.state
        tickers: list[str] = state.get("tickers", []) or []
        data: dict[str, dict] = state.get("smart_money_data", {}) or {}

        # Historical clock: backtest sets state["as_of"]; live falls back to None
        # (the extractor ignores it for clock-free features).
        as_of = state.get("as_of") or None

        # ``smart_money_data`` is structured as:
        #   {"politicians":     {ticker: [filing_dict, ...]},
        #    "notable_holders": {ticker: [holder_dict, ...]}}
        #
        # The extractor expects a per-ticker flat dict with keys
        # "politician_trades" and "notable_holders".  We build that here rather
        # than passing the outer dict directly (which would cause the ticker
        # lookup to always return {} and silently degrade to is_no_data=True).
        politicians_by_ticker:     dict[str, list] = data.get("politicians", {})
        notable_holders_by_ticker: dict[str, list] = data.get("notable_holders", {})

        # Build as a list of dicts so make_evidence_callback can iterate them
        # and build its ticker → verdict lookup.  Each dict includes a
        # "ticker" key alongside the AnalystVerdict fields.
        verdicts: list[dict[str, Any]] = []

        for ticker in tickers:
            raw = {
                "politician_trades": politicians_by_ticker.get(ticker, []),
                "notable_holders":   notable_holders_by_ticker.get(ticker, []),
            }
            features = extract_smart_money_features(raw, ticker, as_of=as_of)
            verdict  = derive_smart_money_verdict(features, self.heuristics)
            v_dict   = verdict.model_dump(mode="json")
            v_dict["ticker"] = ticker
            verdicts.append(v_dict)

        # Write the verdict list so the after_agent_callback can read it.
        state["smart_money_verdicts"] = verdicts

        # Surface trace — no-op unless state["_trace"] is set by trace_tick.py.
        _trace_maybe(ctx.session.state, "02_smart_money_verdict", verdicts)

        # No events emitted — pure state mutation, same as TechnicalAnalyst.
        return
        yield  # required to make this an async generator


# Module-level singleton — used directly by unit tests and the analyst_pool
# singleton in agents/analysts/__init__.py.
smart_money_analyst = SmartMoneyAnalyst(heuristics=load_heuristics().smart_money)


def _build_smart_money_analyst(
    heuristics: SmartMoneyHeuristics | None = None,
) -> SmartMoneyAnalyst:
    """Construct a fresh ``SmartMoneyAnalyst`` for the orchestrator factory.

    Args:
        heuristics: Optional pre-loaded ``SmartMoneyHeuristics``.  When
                    ``None``, ``load_heuristics()`` is called to obtain the
                    cached config.

    Returns:
        A new ``SmartMoneyAnalyst`` instance bound to the given heuristics.
    """
    if heuristics is None:
        heuristics = load_heuristics().smart_money
    return SmartMoneyAnalyst(heuristics=heuristics)
