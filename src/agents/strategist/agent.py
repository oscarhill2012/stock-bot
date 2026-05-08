"""Strategist LlmAgent — Gemini Pro fuses signals into target weights."""
from __future__ import annotations

from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.genai import types as genai_types

from agents.risk_gate.lifecycle import StrategistContractViolation, validate_lifecycle_contract
from .prompts import STRATEGIST_INSTRUCTION
from .schema import StrategistDecision


def _strategist_validation_callback(
    callback_context: CallbackContext,
) -> Optional[genai_types.Content]:
    """Validate strategist output: exhaustive weights + lifecycle contracts."""
    state = callback_context.state
    decision_raw = state.get("strategist_decision")
    if not decision_raw:
        return None

    tickers: list[str] = state.get("tickers", [])
    decision = (
        StrategistDecision.model_validate(decision_raw)
        if isinstance(decision_raw, dict)
        else decision_raw
    )

    # Exhaustive weights check
    emitted = set(decision.target_weights.keys())
    missing = [t for t in tickers if t not in emitted]
    if missing:
        return genai_types.Content(
            parts=[genai_types.Part(text=f"You missed weights for: {missing}. Emit a weight for ALL tickers.")],
            role="user",
        )

    # Off-watchlist check
    extras = [t for t in emitted if t not in tickers]
    if extras:
        return genai_types.Content(
            parts=[genai_types.Part(text=f"You included off-watchlist tickers: {extras}. Only include watchlist tickers.")],
            role="user",
        )

    # Lifecycle contract check — only validate if there are open positions
    # (new openings require thesis; closings require reasons)
    current_weights = {
        ticker: 0.05  # stub current weight for validation
        for ticker in state.get("positions", {}).keys()
    }
    if current_weights:  # only check lifecycle for existing positions
        try:
            validate_lifecycle_contract(
                new_weights=decision.target_weights,
                current_weights=current_weights,
                new_positions=decision.new_positions,
                close_reasons=decision.close_reasons,
            )
        except StrategistContractViolation as e:
            return genai_types.Content(
                parts=[genai_types.Part(text=f"Lifecycle contract violation: {e}. Fix and resubmit.")],
                role="user",
            )

    return None


strategist_agent = LlmAgent(
    name="Strategist",
    model="gemini-2.5-pro",
    instruction=STRATEGIST_INSTRUCTION,
    output_schema=StrategistDecision,
    output_key="strategist_decision",
    after_agent_callback=_strategist_validation_callback,
)
