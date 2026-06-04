"""risk_gate package — public API.

Exports both the agent class and the input-error exception so downstream
callers can import from the package root rather than drilling into the
implementation module.
"""
from agents.risk_gate.agent import RiskGateAgent, RiskGateInputError

__all__ = [
    "RiskGateAgent",
    "RiskGateInputError",
]
