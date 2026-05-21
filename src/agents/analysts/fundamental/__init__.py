"""Fundamental analyst package.

Public API:
- ``build_fundamental_analyst``: factory returning a ``YieldingAnalystWrapper``
  over the Fundamental ``LlmAgent``.  Reads its model ID from
  ``config/models.json`` via ``src.config.models.get_models_config``.  The
  single construction path for both production and tests — there is no
  module-level singleton (removed 2026-05-21).
"""
from .agent import build_fundamental_analyst

__all__ = ["build_fundamental_analyst"]
