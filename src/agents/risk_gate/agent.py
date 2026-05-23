"""RiskGate BaseAgent — deterministic constraints + order generation."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Final

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions

from observability.trace import _trace_maybe
from orchestrator.state import MIN_HELD_WEIGHT

from .constraints import apply_constraints
from .orders import weights_to_orders

# Hold and update are no-trade stances — risk caps are irrelevant.
# They pass through unchanged and the executor's _run_async_impl
# skips broker dispatch for them (resolve_broker_call returns None).
_NO_RISK_GATE_INTENTS: Final[frozenset[str]] = frozenset({"hold", "update"})


class RiskGateAgent(BaseAgent):
    """Pure-Python deterministic agent that sits between the Strategist and the Executor.

    Responsibilities:
    1. Clamp the strategist's target weights to satisfy hard risk rules.
    2. Validate position lifecycle contracts (close_reasons for any closing).
    3. Convert the clamped weights into concrete broker Orders.

    No LLM calls — this agent is fast and fully deterministic.
    """

    name: str = "RiskGate"
    broker: Any = None

    model_config = {"arbitrary_types_allowed": True}

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        from agents.strategist.schema import StrategistDecision

        state = ctx.session.state
        decision_raw = state.get("strategist_decision")
        if not decision_raw:
            return

        decision = (
            StrategistDecision.model_validate(decision_raw)
            if isinstance(decision_raw, dict)
            else decision_raw
        )

        proposed = dict(decision.target_weights)

        # Strip hold/update stances from ``proposed`` before clamping.
        # These stances carry no weight change — the executor will skip
        # broker dispatch for them (``resolve_broker_call`` returns ``None``).
        # Leaving their tickers in the proposed dict would run the clamp
        # logic against a stale/zero weight, which is semantically wrong.
        _stance_intents = {
            s.ticker: s.intent
            for s in (decision.stances or [])
            if s.intent is not None
        }
        for _ticker, _intent in list(_stance_intents.items()):
            if _intent in _NO_RISK_GATE_INTENTS:
                # Preserve the current weight in ``proposed`` unchanged —
                # do not clip or zero it out.
                # If the ticker is missing from target_weights (expected),
                # we simply leave it absent from ``proposed`` too.
                proposed.pop(_ticker, None)

        # Snapshot pre-clamp weights for lifecycle validation below.
        original_weights = dict(proposed)

        # Surface trace — record the weights entering the clamp loop.
        _trace_maybe(state, "06_risk_gate_in", {"proposed_weights": proposed})

        if self.broker:
            portfolio = await self.broker.get_portfolio()
            current_weights = portfolio.current_weights()

            # Build a price map from portfolio positions, then fill any gaps
            # from FakeBroker's injected _prices (used in tests).
            prices = {t: pos.last_price for t, pos in portfolio.positions.items()}
            if hasattr(self.broker, "_prices"):
                for t, p in self.broker._prices.items():
                    if t not in prices:
                        prices[t] = p
        else:
            current_weights = {}
            prices = {}

        # Apply all hard constraints in order; returns telemetry for logging.
        clamps = apply_constraints(proposed, current_weights)

        # Lifecycle check — only closing positions need a recorded reason.
        # New-open validation is handled earlier by the Strategist callback.
        for t, new_w in original_weights.items():
            was_open  = current_weights.get(t, 0.0) >= MIN_HELD_WEIGHT
            will_be_open = new_w >= MIN_HELD_WEIGHT
            if was_open and not will_be_open and t not in decision.close_reasons:
                from agents.strategist.derivation import StrategistContractViolation
                raise StrategistContractViolation(
                    f"Closing {t} ({current_weights.get(t)} -> {new_w}) without close_reason"
                )

        orders = weights_to_orders(proposed, portfolio, prices) if self.broker else []

        # Snapshot the JSON-friendly payloads into local variables so the
        # trace (below) and the yielded ``state_delta`` (further below)
        # both reference the same in-memory list rather than reading
        # back through ``state`` (which, post-Rule-1, the agent no longer
        # writes to directly).
        final_orders        = [o.model_dump() for o in orders]
        risk_clamps_applied = [c.model_dump() for c in clamps]

        # Surface trace — record clamped weights and generated orders.
        # Reads from the local variables, not from ``state``, because the
        # state_delta has not been merged yet at this point.
        _trace_maybe(state, "06_risk_gate_out", {
            "clamped_weights": proposed,
            "orders":          final_orders,
            "clamps":          risk_clamps_applied,
        })

        # Contract Rule 1 — yield a single Event whose state_delta
        # carries both writes.  RiskGate's output handshake to the
        # Executor (final_orders) and to observability
        # (risk_clamps_applied) is one logical step; co-emitting keeps
        # the merge atomic on the SessionService.  See
        # ``docs/contract-invariants.md`` §C-Rule 1.
        yield Event(
            author        = self.name,
            invocation_id = ctx.invocation_id,
            actions       = EventActions(state_delta={
                "final_orders":        final_orders,
                "risk_clamps_applied": risk_clamps_applied,
            }),
        )


# Module-level singleton — pipeline uses RiskGateAgent(broker=...) factory instead.
risk_gate_agent = RiskGateAgent()
