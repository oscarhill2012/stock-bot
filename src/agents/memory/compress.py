"""Day-digest compressor. Concat fast path; LLM fallback when over budget."""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from .schema import BufferEntry

DIGEST_BUDGET = 2000
_compress_llm = None


def set_compress_llm(fn: Callable[[str, str], Awaitable[str]] | None) -> None:
    """Inject a test stub. Pass None to restore default."""
    global _compress_llm
    _compress_llm = fn


async def compress(
    prev_digest: str,
    evicted_entry: BufferEntry,
    llm_fn: Callable[[str, str], Awaitable[str]] | None = None,
) -> str:
    """Merge an evicted BufferEntry into the day digest.

    Fast path: simple concat when combined length < budget.
    LLM path: compress via Gemini Flash when over budget.
    """
    fn = llm_fn or _compress_llm or _default_llm_compress
    appended = f"{prev_digest}\n[{evicted_entry.decision_tag}] {evicted_entry.reasoning_summary}"
    if len(appended) <= DIGEST_BUDGET:
        return appended.strip()
    return await fn(prev_digest, evicted_entry.reasoning_summary)


async def _default_llm_compress(prev_digest: str, new_summary: str) -> str:
    """Compress via Gemini Flash. Returns <=2000 chars."""
    from google import genai  # type: ignore[import]

    prompt = (
        f"You are a financial decision log compressor. "
        f"Existing log (<=2000 chars):\n{prev_digest}\n\n"
        f"New entry to incorporate:\n{new_summary}\n\n"
        f"Rewrite the log incorporating the new entry, keeping it under 2000 characters. "
        f"Preserve key decisions and patterns. Return only the updated log."
    )
    client = genai.Client()
    response = client.models.generate_content(
        model="gemini-2.0-flash-001",
        contents=prompt,
    )
    result = response.text[:DIGEST_BUDGET]
    return result
