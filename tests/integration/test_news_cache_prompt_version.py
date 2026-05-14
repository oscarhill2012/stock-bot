"""Integration test: bumping NEWS_PROMPT_VERSION invalidates the news cache.

Same harness as ``test_news_cache_roundtrip``, but between the first and
second runs the ``NEWS_PROMPT_VERSION`` constant is monkeypatched to a new
value.  The second ``before`` call must return ``None`` (cache miss) because
the stored prompt version no longer matches.
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

def test_prompt_version_bump_busts_cache(cache_root, monkeypatch):
    """Bumping NEWS_PROMPT_VERSION must produce a cache miss on the 2nd run.

    The cache entry written by the first run stores the old prompt version.
    After the monkeypatch the ``before`` hook reads the new version and the
    stored entry no longer matches -> miss.
    """
    before, after = _build_news_cache_callbacks()

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

    after(ctx, llm_response=None)

    # --- Bump the prompt version via monkeypatch ---
    import agents.analysts.news.agent as news_agent_mod
    import agents.analysts.report_cache as cache_mod

    monkeypatch.setattr(cache_mod, "NEWS_PROMPT_VERSION", "v2-test")
    monkeypatch.setattr(news_agent_mod, "NEWS_PROMPT_VERSION", "v2-test")

    # Rebuild the callbacks so they pick up the patched version string.
    # (The closures capture NEWS_PROMPT_VERSION at call time via the module
    # reference, so re-building ensures the new string is used.)
    before2, after2 = _build_news_cache_callbacks()

    ctx.state.pop("news_verdicts", None)

    # The prompt version no longer matches -> cache miss.
    result = before2(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after bumping NEWS_PROMPT_VERSION, "
        "but before-callback returned non-None (spurious cache hit)."
    )
