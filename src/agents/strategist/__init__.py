"""Strategist agent package.

Public API:
- ``build_strategist``: the single construction path for the production Strategist
  branch (``SequentialAgent[ContextShim, LlmAgent]``).  Reads its model ID from
  ``config/models.json`` via ``src.config.models.get_models_config``.

History note: pre-2026-05-21 this module re-exported a ``strategist_agent``
module-level singleton.  That singleton carried its own ``_STRATEGIST_MODEL``
literal that drifted independently from the *live* literal in
``orchestrator.pipeline._build_strategist`` — a 2026-05-20 model swap on the
singleton silently no-op'd because production read the other literal.  Both
the singleton and the shadow constant are now gone; every caller goes
through :func:`build_strategist`.
"""

from .agent import build_strategist

__all__ = ["build_strategist"]
