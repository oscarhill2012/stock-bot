"""Loader for ``config/models.json`` — the single source of truth for every
LLM and embedding model ID consumed across the pipeline.

Why this exists
---------------
Before this loader landed, each agent module hardcoded its own ``gemini-…``
string literal.  That seemed harmless until a model swap on the strategist's
``_STRATEGIST_MODEL`` constant silently no-op'd: the literal that the live
pipeline actually consumed lived in a different file (``pipeline.py``) and
nobody noticed for several backtest runs.  Centralising every model selection
behind one JSON file means a model swap is exactly one edit, in one place,
with no shadow truths to drift against.

Convention — module owns its slot
---------------------------------
Each agent module reads the model ID for its own role from this config at
construction time.  The *value* lives in JSON; the *selection of which slot
to read* lives in the agent's own module.  This keeps construction surfaces
honest — ``pipeline.py`` does not pick the strategist's model any more than
it picks the news analyst's prompt template.

Hot-reload semantics
--------------------
The ``@lru_cache(maxsize=1)`` decorator on :func:`get_models_config` means
the JSON file is read exactly once per process.  Editing the file at runtime
has no effect — a process restart is required.  This matches the
``src/config/strategist.py`` pattern.  ``load_models_config(path=...)`` is the
test hook for feeding a custom JSON path without touching the source tree.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

# Project-root-relative default path.  The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather than
# relative to this file.  Matches the convention in ``src/config/strategist.py``.
_DEFAULT_PATH = Path("config/models.json")


class ModelsConfig(BaseModel):
    """Top-level shape of ``config/models.json``.

    Every field is a model identifier string.  Adding a new LLM call site means
    adding a new field here, populating ``config/models.json``, and updating
    ``config/README.md``.  The Pydantic validation step refuses any payload
    missing one of these keys, so a new field becomes a hard import-time error
    until the JSON is updated — a deliberate forcing function.

    Attributes
    ----------
    strategist:
        Model ID for the Strategist ``LlmAgent`` (consumed by
        ``src/agents/strategist/agent.py::build_strategist``).
    news_analyst:
        Model ID for the News analyst ``LlmAgent`` (consumed by
        ``src/agents/analysts/news/agent.py::build_news_analyst``).
    fundamental_analyst:
        Model ID for the Fundamental analyst ``LlmAgent`` (consumed by
        ``src/agents/analysts/fundamental/agent.py::build_fundamental_analyst``).
    memory_compressor:
        Model ID for the day-digest LLM compressor (consumed by
        ``src/agents/memory/compress.py::_default_llm_compress``).  Only
        invoked when the concatenated digest exceeds ``DIGEST_BUDGET``.
    memory_embedding:
        Embedding model ID for the memory-buffer dedup embedder (consumed by
        ``src/agents/memory/embeddings.py::_default_embed``).  Distinct family
        (text-embedding-005, not Gemini chat), but shares the same
        "where does this live" problem so it belongs in the same config.
    """

    strategist:          str = Field(min_length=1)
    news_analyst:        str = Field(min_length=1)
    fundamental_analyst: str = Field(min_length=1)
    memory_compressor:   str = Field(min_length=1)
    memory_embedding:    str = Field(min_length=1)


def load_models_config(*, path: Path | None = None) -> ModelsConfig:
    """Read and validate ``config/models.json``.

    Parameters
    ----------
    path:
        Override the default path.  Useful for tests that want to feed a
        temporary JSON file without touching the source tree.

    Returns
    -------
    ModelsConfig
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist at the resolved path.
    json.JSONDecodeError
        If the file content is not valid JSON.
    pydantic.ValidationError
        If the parsed payload fails schema validation (e.g. a missing or
        empty model ID).
    """

    p = path or _DEFAULT_PATH

    # Read the raw JSON.  We strip a leading ``_comment`` field if present —
    # JSON has no native comment syntax and operators sometimes want a header
    # note inside the file itself; an underscore-prefixed key is the
    # convention.  Pydantic would otherwise refuse to validate due to the
    # extra field.
    payload = json.loads(p.read_text(encoding="utf-8"))
    payload.pop("_comment", None)

    return ModelsConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_models_config() -> ModelsConfig:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is read exactly
    once per process.  A process restart is required after editing
    ``config/models.json`` — there is no hot-reload path because Pydantic
    field defaults baked at import time would silently disagree with any
    fresh read.  Mirrors the semantics of
    :func:`src.config.strategist.get_strategist_config`.

    Returns
    -------
    ModelsConfig
        Validated configuration singleton.
    """

    return load_models_config()


def _reset_cache() -> None:
    """Clear the ``lru_cache``.  Test hook only — never call from production.

    Use this in test fixtures that mutate ``config/models.json`` (e.g. via
    ``monkeypatch``) so subsequent reads pick up the new content.  Mirrors
    the equivalent helper in ``src/config/strategist.py``.
    """

    get_models_config.cache_clear()
