"""Analyst pool — exposes the per-analyst factories used by the pipeline.

The production analyst pool is constructed by
``orchestrator.pipeline._build_analyst_pool`` from these factories.  This
package re-exports them for tests and ad-hoc tooling that want to construct
an individual analyst in isolation.

Pre-2026-05-21 this module eagerly imported per-analyst module-level
singletons (``fundamental_analyst``, ``news_analyst``, etc.) and built an
unused ``analyst_pool`` ParallelAgent at import time.  That entire side
effect is gone — the only construction path is now through the factories
below, called explicitly by the pipeline or by tests.
"""

from .fundamental.agent import build_fundamental_analyst
from .news.agent import build_news_analyst

__all__ = [
    "build_fundamental_analyst",
    "build_news_analyst",
]
