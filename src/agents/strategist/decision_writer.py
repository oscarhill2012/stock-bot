"""Persist per-ticker stances to ``TickerStanceRow`` after each strategist tick.

The ``StrategistDecisionWriter`` is a lightweight ADK ``BaseAgent`` that reads
``state["strategist_decision"]`` from the invocation context and calls
``save_ticker_stance`` once per ticker.  The lifecycle action is read directly
from ``stance.intent`` — no weight-comparison derivation.  It yields no events
— it is a pure side-effectful write step wired into the orchestrator pipeline.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from data.timeguard import resolve_as_of


class StrategistDecisionWriter(BaseAgent):
    """ADK agent that persists per-ticker stances to the ``ticker_stances`` table.

    Reads ``state["strategist_decision"]`` (a ``StrategistDecision`` dump or
    instance) from the invocation context state, then writes one
    ``TickerStanceRow`` per ticker via ``save_ticker_stance``.  The lifecycle
    action column is populated from ``stance.intent`` directly.

    The agent is a no-op (and yields nothing) when either ``db_session`` is
    ``None`` or ``strategist_decision`` is absent/falsy in state.
    """

    name: str = "StrategistDecisionWriter"
    db_session: Any = None

    # Allow SQLAlchemy session and other non-Pydantic types as field values.
    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        """Drain strategist stances from state and write them to the database.

        Yields nothing; returns early on no-op short-circuits.
        """
        # No-op short-circuit: no database session available.
        if self.db_session is None:
            return
            yield  # pragma: no cover — generator gate

        state = ctx.session.state

        # No-op short-circuit: no decision was emitted this tick.
        raw_decision = state.get("strategist_decision")
        if not raw_decision:
            return
            yield  # pragma: no cover — generator gate

        # Lazy imports keep the module importable without ADK in test environments
        # and keep the orchestrator.persistence dependency lazy so this
        # module imports without a configured ORM session.
        from agents.strategist.schema import StrategistDecision
        from orchestrator.persistence import save_ticker_stance

        # Accept either an already-validated instance or a raw dict from state.
        if isinstance(raw_decision, StrategistDecision):
            decision = raw_decision
        else:
            decision = StrategistDecision.model_validate(raw_decision)

        # Timestamp shared across all rows written in this invocation.
        # Prefer state["as_of"] (set by the backtest driver to the historical
        # tick timestamp) so replay is deterministic.  Fall back to wall-clock
        # only on live runs where as_of is absent.
        raw_as_of = state.get("as_of")
        recorded_at = resolve_as_of(
            raw_as_of,
            allow_wallclock=True,
            site="decision_writer",
        )

        # Loop: one DB row per stance in the decision.
        for stance in decision.stances:
            # Read intent directly from the stance — no weight-comparison derivation.
            # Fallback to "update" (the iter-3 no-trade verb) as a safety net;
            # intent=None should have been rejected upstream by derive_decision_fields,
            # so this branch is unreachable in production (derivation raises on None).
            action = stance.intent or "update"
            save_ticker_stance(
                self.db_session,
                tick_id=state.get("tick_id", "unknown"),
                decision_tag=decision.decision_tag,
                recorded_at=recorded_at,
                stance=stance.model_dump(mode="json"),
                lifecycle_action=action,
            )

        self.db_session.commit()
        return
        yield  # required to make this a generator function


def build_strategist_decision_writer(db_session=None) -> StrategistDecisionWriter:
    """Factory that constructs a ``StrategistDecisionWriter`` bound to ``db_session``."""
    return StrategistDecisionWriter(db_session=db_session)
