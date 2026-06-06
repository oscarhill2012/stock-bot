"""Strategist enricher smoke — replaces the legacy-callback minimal-schema test.

The original ``test_strategist_minimal_schema_no_retry.py`` proved that a
clean intent-form ``StrategistLLMDecision`` flows through
``_strategist_validation_callback`` unchanged.  Plan 07 retired that
callback; the same invariant is now expressed by driving the production
``StrategistEnricher`` BaseAgent with a stub LlmAgent.

We assert:
  1. The stub LLM emits its narrow payload via ``state_delta`` (mirroring
     ``LlmAgent``'s ``output_key`` write) exactly once.
  2. The sequenced ``StrategistEnricher`` rewrites
     ``state["strategist_decision"]`` to the enriched dump.
  3. ``derive_decision_fields`` populates ``target_weights`` as expected
     from the input stances.

No live API.  No retry wrapper (the retry layer is its own concern and is
covered separately).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types as genai_types

from agents.strategist.enricher import StrategistEnricher
from agents.strategist.schema import StrategistLLMDecision
from agents.strategist.stance_schema import TickerStance
from broker.portfolio import Portfolio

# AAPL buy weight.  0.04 < max_delta_per_buy (0.20 from config/risk_gate.json),
# so it passes the schema cap; the portfolio starts empty, so the derived
# target weight is 0.0 + 0.04 = 0.04.
_BUY_WEIGHT = 0.04


class _StubLlmAgent(BaseAgent):
    """Yields a single Event whose ``state_delta`` writes a clean narrow decision.

    Replaces the real LlmAgent so the test never touches Vertex.  Mirrors
    what ``LlmAgent`` does with ``output_key`` on a successful call: it
    writes the *narrow* ``StrategistLLMDecision`` shape (the enricher widens
    it into the full ``StrategistDecision``).
    """

    name: str = "Strategist"

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Emit one Event whose state_delta carries a narrow StrategistLLMDecision.

        Mirrors the write that a real LlmAgent performs via ``output_key`` on a
        successful Vertex call.  The enricher receives this narrow shape and widens
        it into the full ``StrategistDecision`` with derived fields.
        """
        # Build the narrow LLM-shape decision.  Two stances exercise the buy
        # and update verbs; the buy weight stays inside the per-trade
        # buy-delta cap.  decision_tag / reasoning / confidence are required
        # by StrategistLLMDecision and are exactly what a real LlmAgent emits.
        decision = StrategistLLMDecision(
            stances=[
                TickerStance(
                    ticker    = "AAPL",
                    intent    = "buy",
                    weight    = _BUY_WEIGHT,
                    rationale = "Strong earnings momentum and AI tailwind.",
                ),
                TickerStance(
                    ticker    = "MSFT",
                    intent    = "update",
                    rationale = "Prose-only update — no trade this tick.",
                ),
            ],
            decision_tag = "buy_aapl_update_msft",
            reasoning    = "Initiating AAPL; updating MSFT thesis.",
            thesis       = None,
            confidence   = 0.72,  # arbitrary mid-range value; not asserted (not load-bearing)
        ).model_dump(mode="json")

        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "strategist_decision": decision,
            }),
        )


@pytest.mark.asyncio
async def test_enricher_rewrites_decision_to_enriched_dump() -> None:
    """A clean intent-form decision flows through the enricher to enriched shape."""

    branch = SequentialAgent(
        name       = "StrategistBranch",
        sub_agents = [_StubLlmAgent(), StrategistEnricher()],
    )

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name = "test",
        user_id  = "u",
        state    = {
            # Seed the keys the enricher reads — same shape as production.
            # ``portfolio`` is mandatory: Portfolio.from_state_value raises
            # on None (no silent empties — audit A-014/A-071).
            # ``as_of`` is intentionally absent — the enricher falls back to
            # wall-clock time via resolve_as_of(..., allow_wallclock=True).
            "tickers":   ["AAPL", "MSFT"],
            "watchlist": ["AAPL", "MSFT"],
            "portfolio": Portfolio(cash=1000.0).model_dump(mode="json"),
            "tick_id":   "tick-001",
            "user:active_stances": {},
            "user:active_stances_initialised": False,
        },
    )

    runner = Runner(agent=branch, app_name="test", session_service=session_service)

    # Drive the runner; we don't need user input — the stub yields proactively.
    async for _ in runner.run_async(
        user_id     = "u",
        session_id  = session.id,
        new_message = genai_types.Content(parts=[genai_types.Part.from_text(text="")]),
    ):
        pass

    final = await session_service.get_session(
        app_name = "test", user_id = "u", session_id = session.id,
    )
    enriched = final.state["strategist_decision"]

    # Enriched dump carries the derived fields the LLM doesn't emit directly.
    assert "target_weights" in enriched, (
        "StrategistEnricher should populate target_weights from stances"
    )
    assert enriched["target_weights"].get("AAPL") == pytest.approx(_BUY_WEIGHT)
    # MSFT has no weight (update verb) — derivation should leave it at 0.0.
    assert enriched["target_weights"].get("MSFT", 0.0) == pytest.approx(0.0)

    # 3. active_stances_initialised flip — the enricher's one-shot write.
    assert final.state["user:active_stances_initialised"] is True
