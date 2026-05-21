"""News analyst package — LlmAgent scoped to news headlines only.

Public API:
- ``build_news_analyst``: factory returning a ``YieldingAnalystWrapper`` over
  the News ``LlmAgent``.  Reads its model ID from ``config/models.json`` via
  ``src.config.models.get_models_config``.  The single construction path for
  both production and tests — there is no module-level singleton (removed
  2026-05-21).
"""
from __future__ import annotations

from .agent import build_news_analyst

__all__ = ["build_news_analyst"]
