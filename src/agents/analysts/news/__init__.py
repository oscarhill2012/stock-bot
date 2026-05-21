"""News analyst package — per-ticker fan-out (Phase 9).

Public API:
- ``build_news_branch``: factory returning a ``SequentialAgent`` of
  ``[NewsFetchAgent, *per-ticker branches, NewsJoinerAgent]``.
  The single construction path for both production and tests.

The legacy ``build_news_analyst`` (one LlmAgent over a VerdictBatch) is
retired in Phase 9; call sites are updated in Tasks 12–15.
"""
from __future__ import annotations

from .agent import build_news_branch

__all__ = ["build_news_branch"]
