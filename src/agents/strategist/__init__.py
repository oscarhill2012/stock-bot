"""Strategist agent package.

Public API:
- ``strategist_agent``: module-level singleton exposed here for convenience.

Re-export audit (FU-09): no external caller uses ``from agents.strategist import
strategist_agent`` — the smoke test (``tests/integration/test_strategist_v2_smoke.py``)
imports directly from ``agents.strategist.agent``.  The re-export is retained so that
package consumers have a stable top-level import path; future maintainers should not
remove it without checking for that usage.
"""
from .agent import strategist_agent

__all__ = ["strategist_agent"]
