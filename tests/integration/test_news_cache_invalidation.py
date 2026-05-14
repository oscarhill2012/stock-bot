"""Integration test: adding a new article invalidates the news cache.

Same harness as ``test_news_cache_roundtrip``, but between the first and
second runs a new article is appended to the article list.  The second
``before`` call must return ``None`` (cache miss) because the input hash has
changed.
"""
from __future__ import annotations

import json

import pytest

from agents.analysts.news.agent import _build_news_cache_callbacks
from data.models import NewsArticle

# ---------------------------------------------------------------------------
# Shared fixture (mirrors test_news_cache_roundtrip)
# ---------------------------------------------------------------------------

@pytest.fixture()
def cache_root(tmp_path, monkeypatch):
    """Point AnalystsConfig at a tmp_path cache directory.

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

def test_new_article_busts_cache(cache_root):
    """Adding a new article between runs must produce a cache miss on the 2nd run."""
    before, after = _build_news_cache_callbacks()

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

    # Simulate LLM output.
    ctx.state["news_verdicts"] = {
        "verdicts": [{
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
        }],
    }

    # Persist to cache.
    after(ctx, llm_response=None)

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
