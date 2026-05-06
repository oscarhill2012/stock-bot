"""RiskGate BaseAgent — deterministic constraints + order generation."""
from __future__ import annotations

from typing import Any, AsyncGenerator

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .constraints import apply_constraints
from .lifecycle import StrategistContractViolation, validate_lifecycle_contract
from .orders import weights_to_orders
from orchestrator.state import MIN_HELD_WEIGHT


class RiskGateAgent(BaseAgent):
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
        # Keep original weights for lifecycle validation (pre-clamp)
        original_weights = dict(proposed)

        # Current weights from portfolio
        if self.broker:
            portfolio = await self.broker.get_portfolio()
            current_weights = portfolio.current_weights()
            # Start with position prices; supplement with broker's internal prices if available
            prices = {
                t: pos.last_price for t, pos in portfolio.positions.items()
            }
            # FakeBroker exposes _prices for test convenience
            if hasattr(self.broker, "_prices"):
                for t, p in self.broker._prices.items():
                    if t not in prices:
                        prices[t] = p
        else:
            current_weights = {}
            prices = {}

        clamps = apply_constraints(proposed, current_weights)

        # Lifecycle check: only validate closings (RiskGate enforces close_reasons;
        # new open validation is handled by the Strategist callback)
        for t, new_w in original_weights.items():
            was_open = current_weights.get(t, 0.0) >= MIN_HELD_WEIGHT
            will_be_open = new_w >= MIN_HELD_WEIGHT
            if was_open and not will_be_open and t not in decision.close_reasons:
                from agents.risk_gate.lifecycle import StrategistContractViolation
                raise StrategistContractViolation(
                    f"Closing {t} ({current_weights.get(t)} -> {new_w}) without close_reason"
                )

        if self.broker:
            orders = weights_to_orders(proposed, portfolio, prices)
        else:
            orders = []

        state["final_orders"] = [o.model_dump() for o in orders]
        state["risk_clamps_applied"] = [c.model_dump() for c in clamps]
        return
        yield


risk_gate_agent = RiskGateAgent()
