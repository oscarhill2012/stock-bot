"""Integration test: bumping NEWS_PROMPT_VERSION invalidates the news cache.

Same harness as ``test_news_cache_roundtrip``, but between the first and
second runs the prompt-version string is changed.  The second ``before`` call
must return ``None`` (cache miss) because the stored prompt version no longer
matches.

The ``cache_root`` fixture and ``_Ctx`` stub are defined in ``conftest.py``
and auto-discovered by pytest.

NOTE: the ``_after`` hook now reads verdicts from ``llm_response.content``
directly (B22 bug-fix).  Tests must pass a fake response object.

NOTE: because ``prompt_version`` is now a plain string argument captured at
factory-invocation time (rather than a module-level name resolved lazily), the
test passes the "bumped" version directly to a second ``make_report_cache_callbacks``
call rather than monkeypatching the module attribute.
"""
from __future__ import annotations

import json
import types

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.report_cache import NEWS_PROMPT_VERSION, news_hash_inputs
from data.models import NewsArticle
from tests.integration.conftest import _Ctx


def _fake_llm_response(verdicts: list[dict]) -> types.SimpleNamespace:
    """Minimal fake LLM response whose ``.content.parts[0].text`` is the JSON payload."""
    text    = json.dumps({"verdicts": verdicts})
    part    = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(content=content)


def _make_news_callbacks(prompt_version: str = NEWS_PROMPT_VERSION):
    """Construct news cache callbacks via the shared factory.

    Parameters
    ----------
    prompt_version:
        Prompt-version string to bake into the callbacks.  Defaults to the
        real constant; pass a different string to simulate a version bump.
    """
    return make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = prompt_version,
        data_state_key     = "news_data",
        verdicts_state_key = "news_verdicts",
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = "03_news_llm",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_prompt_version_bump_busts_cache(cache_root):
    """Bumping the prompt version must produce a cache miss on the 2nd run.

    The cache entry written by the first run stores the original prompt version.
    The second pair of callbacks is built with a different version string, so
    the stored entry no longer matches -> miss.
    """
    before, after = _make_news_callbacks(NEWS_PROMPT_VERSION)

    articles = [
        NewsArticle(
            url="https://x",
            headline="t",
            summary="s",
            published_at="2026-05-13T10:00:00",
            source="src",
            ticker="AAPL",
        ).model_dump()
    ]

    ctx = _Ctx({
        "tickers":   ["AAPL"],
        "news_data": {"AAPL": {"news": articles}},
    })

    # --- First run — write to cache with the original prompt version ---
    assert before(ctx, llm_request=None) is None

    verdicts_payload = [{
        "ticker":       "AAPL",
        "lean":         "neutral",
        "magnitude":    0.3,
        "confidence":   0.7,
        "rationale":    "x",
        "key_factors":  [],
        "is_no_data":   False,
        "report": {
            "summary": "s",
            "drivers": [
                {"name": "n1", "direction": "neutral", "weight": 0.5, "body": "body one"},
                {"name": "n2", "direction": "neutral", "weight": 0.5, "body": "body two"},
            ],
        },
    }]

    after(ctx, llm_response=_fake_llm_response(verdicts_payload))

    # --- Rebuild with a bumped prompt version (simulates a real version bump) ---
    before2, _ = _make_news_callbacks("v2-test")

    ctx.state.pop("news_verdicts", None)

    # The prompt version no longer matches the stored entry -> cache miss.
    result = before2(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after bumping the prompt version, "
        "but before-callback returned non-None (spurious cache hit)."
    )
