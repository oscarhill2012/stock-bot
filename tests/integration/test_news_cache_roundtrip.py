"""Integration test: News analyst is short-circuited by the report cache on a 2nd run.

Uses the cache-callback harness directly rather than running the full ADK pipeline
— far more hermetic and avoids any live LLM calls or network traffic.

The ``cache_root`` fixture is defined in ``conftest.py`` and redirects
``get_analysts_config()`` to a tmp_path so the test never touches the real
``cache/reports`` directory.  ``_Ctx`` is also imported from there.
"""
from __future__ import annotations

from agents.analysts.news.agent import _build_news_cache_callbacks
from data.models import NewsArticle
from tests.integration.conftest import _Ctx

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_second_run_hits_cache(cache_root):
    """Identical article set on two consecutive runs -> 2nd run short-circuits the LLM."""
    before, after = _build_news_cache_callbacks()

    # Build a minimal article list and serialise to the dict form that the
    # fetch callback stores in state["news_data"][ticker]["news"].
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

    # --- First run — cache miss ---
    # before-callback should return None (miss -> proceed to LLM).
    assert before(ctx, llm_request=None) is None

    # Simulate the LLM having run and written verdicts into state.
    ctx.state["news_verdicts"] = {
        "verdicts": [{
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
        }],
    }

    # after-callback should persist the verdict to disk — no return value.
    after(ctx, llm_response=None)

    # --- Second run with identical inputs — cache hit ---
    ctx.state.pop("news_verdicts", None)
    short_circuit = before(ctx, llm_request=None)

    # The before-callback must return a non-None Content (short-circuit).
    assert short_circuit is not None

    # The cached verdicts must have been written into state.
    assert ctx.state["news_verdicts"]["verdicts"][0]["ticker"] == "AAPL"
