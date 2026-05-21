"""Fundamental analyst package — per-ticker fan-out (Phase 9).

Public API:
- ``build_fundamental_branch``: factory returning a ``SequentialAgent``
  of ``[FundamentalFetchAgent, *per-ticker branches, FundamentalJoinerAgent]``.
  The single construction path for both production and tests.

The legacy ``build_fundamental_analyst`` (one LlmAgent over a VerdictBatch)
is retired in Phase 9; call sites are updated in Tasks 12–15.
"""
from .agent import build_fundamental_branch

__all__ = ["build_fundamental_branch"]
