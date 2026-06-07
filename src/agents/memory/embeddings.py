"""Embedding helper for the memory subsystem."""
from __future__ import annotations

import numpy as np


async def embed(text: str) -> list[float]:
    """Embed text using the configured Vertex AI embedding model.

    Delegates to :func:`_default_embed`, which reads the model ID from
    ``config/models.json::memory_embedding``. Tests stub this by
    monkeypatching :func:`_default_embed` directly.
    """
    return await _default_embed(text)


async def _default_embed(text: str) -> list[float]:
    """Call the configured Vertex AI embedding model via google-genai.

    The model ID is pulled from ``config/models.json::memory_embedding`` via
    :func:`src.config.models.get_models_config` — see the docstring of
    ``src/config/models.py`` for the "module owns its own slot" rationale and
    the 2026-05-20 incident that motivated centralising every model literal.
    """
    from google import genai  # type: ignore[import]
    from tenacity import retry, stop_after_attempt, wait_exponential

    from config.models import get_models_config

    # Pull the embedding model ID from the central config.  This is the
    # single source of truth — see ``config/models.json`` and the loader.
    model_name = get_models_config().memory_embedding

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _call() -> list[float]:
        client = genai.Client()
        result = client.models.embed_content(
            model    = model_name,
            contents = text,
        )
        return result.embeddings[0].values

    return await _call()


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two vectors. Returns value in [-1, 1]."""
    va = np.array(a, dtype=float)
    vb = np.array(b, dtype=float)
    norm_a = np.linalg.norm(va)
    norm_b = np.linalg.norm(vb)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(va, vb) / (norm_a * norm_b))
