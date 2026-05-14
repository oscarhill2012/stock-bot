"""Integration test: News analyst is short-circuited by the report cache on a 2nd run.

Uses the cache-callback harness directly rather than running the full ADK pipeline
— far more hermetic and avoids any live LLM calls or network traffic.

The ``cache_root`` fixture redirects ``get_analysts_config()`` to a tmp_path so
the test never touches the real ``cache/reports`` directory.
"""
from __future__ import annotations

import json

import pytest

from agents.analysts.news.agent import _build_news_cache_callbacks
from data.models import NewsArticle

# ---------------------------------------------------------------------------
# Shared fixture — config redirected to tmp_path
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """Point AnalystsConfig at a tmp_path cache directory.

    Writes a minimal ``analysts.json`` pointing at ``tmp_path/cache``, patches
    the module-level ``_DEFAULT_PATH`` in ``config.analysts``, and clears the
    ``lru_cache`` so the fresh config is loaded. Clears the cache again on
    teardown so subsequent tests start clean.

    Parameters
    ----------
    tmp_path:
        pytest-provided temporary directory (unique per test).
    monkeypatch:
        pytest monkeypatch fixture for safe attribute patching.

    Yields
    ------
    Path
        Absolute path to the tmp cache root (``tmp_path / "cache"``).
    """
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": str(tmp_path / "cache")},
    }))

    from config import analysts as cfg_mod
    cfg_mod.get_analysts_config.cache_clear()
    monkeypatch.setattr(cfg_mod, "_DEFAULT_PATH", cfg_file)

    yield tmp_path / "cache"

    # Teardown — clear so later tests load their own config.
    cfg_mod.get_analysts_config.cache_clear()


# ---------------------------------------------------------------------------
# Minimal state stub
# ---------------------------------------------------------------------------

class _Ctx:
    """Minimal callback-context stub that exposes a mutable ``state`` dict."""

    def __init__(self, state: dict):
        self.state = state


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
