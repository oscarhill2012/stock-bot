"""Embedding helper with injectable provider for tests."""
from __future__ import annotations

import numpy as np

# Default provider — can be overridden in tests via set_embedding_provider()
_embedding_provider = None


def set_embedding_provider(fn) -> None:
    """Override the embed() implementation. Pass None to restore default."""
    global _embedding_provider
    _embedding_provider = fn


async def embed(text: str) -> list[float]:
    """Embed text using Vertex AI text-embedding-005 or the injected provider."""
    if _embedding_provider is not None:
        return await _embedding_provider(text)
    return await _default_embed(text)


async def _default_embed(text: str) -> list[float]:
    """Call Vertex AI text-embedding-005 via google-genai."""
    from google import genai  # type: ignore[import]
    from tenacity import retry, stop_after_attempt, wait_exponential

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=8))
    async def _call() -> list[float]:
        client = genai.Client()
        result = client.models.embed_content(
            model="text-embedding-005",
            contents=text,
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
