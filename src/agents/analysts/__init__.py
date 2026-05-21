"""Analyst pool — exposes the per-analyst branch factories used by the pipeline.

The production analyst pool is constructed by
``orchestrator.pipeline._build_analyst_pool`` from these factories.  This
package re-exports them for tests and ad-hoc tooling that want to construct
an individual analyst branch in isolation.

Phase 9 replaces the monolithic LlmAgent factories (``build_news_analyst``,
``build_fundamental_analyst``) with SequentialAgent branch factories
(``build_news_branch``, ``build_fundamental_branch``) that fan out per ticker.
The old factories are retired; call sites are updated in Tasks 12–15.
"""

from .fundamental.agent import build_fundamental_branch
from .news.agent import build_news_branch

__all__ = [
    "build_fundamental_branch",
    "build_news_branch",
]
