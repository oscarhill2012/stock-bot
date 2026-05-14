"""Integration test: News analyst is short-circuited by the report cache on a 2nd run.

Uses the cache-callback harness directly rather than running the full ADK pipeline
— far more hermetic and avoids any live LLM calls or network traffic.

The ``cache_root`` fixture is defined in ``conftest.py`` and redirects
``get_analysts_config()`` to a tmp_path so the test never touches the real
``cache/reports`` directory.  ``_Ctx`` is also imported from there.

NOTE: the ``_after`` hook now reads verdicts from ``llm_response.content`` directly
(the B22 bug-fix) rather than from state.  Tests must pass a fake response object.
"""
from __future__ import annotations

import json
import types

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.report_cache import NEWS_PROMPT_VERSION, news_hash_inputs
from data.models import NewsArticle
from tests.integration.conftest import _Ctx


def _fake_llm_response(verdicts: list[dict]) -> types.SimpleNamespace:
    """Build a minimal fake LLM response whose content matches the factory's expectations.

    The ``_after`` hook reads ``llm_response.content.parts[0].text`` for the
    JSON payload.  This helper constructs the minimal stub so tests don't need
    to import google.genai.

    Parameters
    ----------
    verdicts:
        List of verdict dicts to embed in the JSON payload.

    Returns
    -------
    types.SimpleNamespace
        A stub whose ``.content.parts[0].text`` is the serialised payload.
    """
    text = json.dumps({"verdicts": verdicts})
    part    = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(content=content)


def _make_news_callbacks(cache_root):
    """Construct the news cache callbacks via the shared factory.

    Mirrors the wiring in ``_build_news_analyst`` so the integration tests
    exercise the same factory path as the real agent.

    Parameters
    ----------
    cache_root:
        Unused directly — the factory reads it from ``get_analysts_config()``,
        which the ``cache_root`` fixture has already redirected to ``tmp_path``.

    Returns
    -------
    tuple[Callable, Callable]
        ``(before, after)`` cache callbacks for the News analyst.
    """
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

def test_second_run_hits_cache(cache_root):
    """Identical article set on two consecutive runs -> 2nd run short-circuits the LLM."""
    before, after = _make_news_callbacks(cache_root)

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

    # Build the verdict payload that the LLM would have returned, then pass it
    # as a fake llm_response object — _after now reads the response directly
    # rather than state (B22 lifecycle bug fix).
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

    # after-callback should persist the verdict to disk — no return value.
    after(ctx, llm_response=_fake_llm_response(verdicts_payload))

    # --- Second run with identical inputs — cache hit ---
    ctx.state.pop("news_verdicts", None)
    short_circuit = before(ctx, llm_request=None)

    # The before-callback must return a non-None Content (short-circuit).
    assert short_circuit is not None

    # The cached verdicts must have been written into state.
    assert ctx.state["news_verdicts"]["verdicts"][0]["ticker"] == "AAPL"
