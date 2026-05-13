"""RiskGate BaseAgent — deterministic constraints + order generation."""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from observability.trace import _trace_maybe
from orchestrator.state import MIN_HELD_WEIGHT

from .constraints import apply_constraints
from .orders import weights_to_orders


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
                from agents.risk_gate.lifecycle import StrategistContractViolation
                raise StrategistContractViolation(
                    f"Closing {t} ({current_weights.get(t)} -> {new_w}) without close_reason"
                )

        orders = weights_to_orders(proposed, portfolio, prices) if self.broker else []

        state["final_orders"] = [o.model_dump() for o in orders]
        state["risk_clamps_applied"] = [c.model_dump() for c in clamps]

        # Surface trace — record clamped weights and generated orders.
        _trace_maybe(state, "06_risk_gate_out", {
            "clamped_weights": proposed,
            "orders": state["final_orders"],
            "clamps": state["risk_clamps_applied"],
        })

        return
        yield  # required to make this an async generator


# Module-level singleton — pipeline uses RiskGateAgent(broker=...) factory instead.
risk_gate_agent = RiskGateAgent()
