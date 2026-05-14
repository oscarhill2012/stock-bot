"""Integration test: adding a new article invalidates the news cache.

Same harness as ``test_news_cache_roundtrip``, but between the first and
second runs a new article is appended to the article list.  The second
``before`` call must return ``None`` (cache miss) because the input hash has
changed.

The ``cache_root`` fixture and ``_Ctx`` stub are defined in ``conftest.py``
and auto-discovered by pytest.

NOTE: the ``_after`` hook now reads verdicts from ``llm_response.content``
directly (B22 bug-fix).  Tests must pass a fake response object.
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


def _make_news_callbacks():
    """Construct news cache callbacks via the shared factory."""
    return make_report_cache_callbacks(
        analyst_name       = "news",
        prompt_version     = NEWS_PROMPT_VERSION,
        data_state_key     = "news_data",
        verdicts_state_key = "news_verdicts",
        hash_inputs        = lambda d: news_hash_inputs((d or {}).get("news") or []),
        trace_label        = "03_news_llm",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_new_article_busts_cache(cache_root):
    """Adding a new article between runs must produce a cache miss on the 2nd run."""
    before, after = _make_news_callbacks()

    # --- First run — two articles ---
    articles_v1 = [
        NewsArticle(
            url="https://a",
            headline="t",
            summary="s",
            published_at="2026-05-13T10:00:00",
            source="src",
            ticker="AAPL",
        ).model_dump(),
        NewsArticle(
            url="https://b",
            headline="t",
            summary="s",
            published_at="2026-05-13T11:00:00",
            source="src",
            ticker="AAPL",
        ).model_dump(),
    ]

    ctx = _Ctx({
        "tickers":   ["AAPL"],
        "news_data": {"AAPL": {"news": articles_v1}},
    })

    # First run: miss -> LLM would run.
    assert before(ctx, llm_request=None) is None

    # Persist to cache via the fake LLM response.
    verdicts_payload = [{
        "ticker":       "AAPL",
        "lean":         "bullish",
        "magnitude":    0.6,
        "confidence":   0.8,
        "rationale":    "x",
        "key_factors":  [],
        "is_no_data":   False,
        "report": {
            "summary": "s",
            "drivers": [
                {"name": "n1", "direction": "bull",    "weight": 0.6, "body": "first driver"},
                {"name": "n2", "direction": "neutral", "weight": 0.4, "body": "second driver"},
            ],
        },
    }]

    after(ctx, llm_response=_fake_llm_response(verdicts_payload))

    # --- Second run — NEW third article added ---
    articles_v2 = articles_v1 + [
        NewsArticle(
            url="https://c",
            headline="breaking",
            summary="big news",
            published_at="2026-05-13T12:00:00",
            source="src",
            ticker="AAPL",
        ).model_dump(),
    ]

    ctx.state["news_data"] = {"AAPL": {"news": articles_v2}}
    ctx.state.pop("news_verdicts", None)

    # The input hash has changed -> cache miss -> before must return None.
    result = before(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after adding a new article, "
        "but before-callback returned non-None (spurious cache hit)."
    )
