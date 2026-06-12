"""News analyst package — per-ticker fan-out (Phase 9).

The branch factory is imported from the submodule directly::

    from agents.analysts.news.agent import build_news_branch

This package ``__init__`` is deliberately kept free of eager ``.agent``
imports to avoid an import cycle with ``report_cache`` (A-096).  When
``report_cache`` loads at module initialisation time, any eager import of
``.agent`` from here would re-enter ``cache_callbacks`` while it is still
partially initialised, causing an ``ImportError``.  Callers should always
import ``build_news_branch`` from ``agents.analysts.news.agent`` directly.
"""
from __future__ import annotations
