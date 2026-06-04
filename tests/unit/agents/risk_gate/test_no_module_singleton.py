"""A-057: the dead module-level RiskGateAgent() singleton must not return.

A brokerless RiskGateAgent silently degrades — Plan 05 Task 3 made broker
prices a hard requirement; an importable agent with no broker bypasses it.
This guard fails if the module-level instance is ever reintroduced.
"""
import agents.risk_gate.agent as _rg


def test_no_module_level_risk_gate_singleton():
    """The risk_gate module must expose no pre-built RiskGateAgent instance."""
    assert not hasattr(_rg, "risk_gate_agent"), (
        "module-level `risk_gate_agent` is dead (A-057); construct via "
        "RiskGateAgent(broker=...) at pipeline wire-up time instead"
    )
