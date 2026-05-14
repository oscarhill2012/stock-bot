"""Integration test: bumping FUNDAMENTAL_PROMPT_VERSION invalidates the Fundamental cache.

Same harness as ``test_fundamental_cache_roundtrip``, but between the first and
second runs ``agents.analysts.report_cache.FUNDAMENTAL_PROMPT_VERSION`` is
monkeypatched to a new value.  The second ``before`` call must return ``None``
(cache miss) because the stored prompt version no longer matches the module-
level constant.

This mirrors ``test_news_cache_prompt_version`` for the Fundamental analyst.

The ``cache_root`` fixture and ``_Ctx`` stub are defined in ``conftest.py``
and auto-discovered by pytest.
"""
from __future__ import annotations

from agents.analysts.fundamental.agent import _build_fundamental_cache_callbacks
from tests.integration.conftest import _Ctx

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_prompt_version_bump_busts_cache(cache_root, monkeypatch):
    """Bumping FUNDAMENTAL_PROMPT_VERSION must produce a cache miss on the 2nd run.

    The cache entry written by the first run contains the old prompt version
    string.  After the monkeypatch, the ``_before`` hook reads the new version
    from the module and the stored entry no longer matches -> miss.

    Note: callbacks are rebuilt after the monkeypatch so that the new closures
    capture the updated version constant.  The cache is read from the same
    ``tmp_path`` directory populated by the first run.
    """
    before, after = _build_fundamental_cache_callbacks()

    # Minimal state — same shape as the roundtrip test.
    ctx = _Ctx({
        "tickers": ["AAPL"],
        "fundamental_data": {
            "AAPL": {
                "ratios":  {"ticker": "AAPL", "trailing_pe": 22.1},
                "filings": [],
                "insider": None,
            }
        },
    })

    # --- First run — write to cache with the original prompt version ---
    assert before(ctx, llm_request=None) is None

    ctx.state["fundamental_verdicts"] = {
        "verdicts": [{
            "ticker":       "AAPL",
            "lean":         "neutral",
            "magnitude":    0.2,
            "confidence":   0.6,
            "rationale":    "Unremarkable quarter.",
            "key_factors":  [],
            "is_no_data":   False,
            "report": {
                "summary": "Metrics are broadly in line with expectations.",
                "drivers": [
                    {"name": "PE near fair value",     "direction": "neutral", "weight": 0.5, "body": "Trailing PE of 22 sits at the sector median."},
                    {"name": "No material news flow",  "direction": "neutral", "weight": 0.5, "body": "No catalysts expected in the next 30 days."},
                ],
            },
        }],
    }

    after(ctx, llm_response=None)

    # --- Bump the prompt version via monkeypatch ---
    # Patch both the cache module (read by read_cache) and the agent module
    # (which imports the constant into its own namespace via
    # ``from agents.analysts.report_cache import FUNDAMENTAL_PROMPT_VERSION``).
    import agents.analysts.fundamental.agent as fundamental_agent_mod
    import agents.analysts.report_cache as cache_mod

    monkeypatch.setattr(cache_mod,              "FUNDAMENTAL_PROMPT_VERSION", "v-test-2")
    monkeypatch.setattr(fundamental_agent_mod,  "FUNDAMENTAL_PROMPT_VERSION", "v-test-2")

    # Rebuild the callbacks so the new closures capture the patched version string.
    before2, after2 = _build_fundamental_cache_callbacks()

    ctx.state.pop("fundamental_verdicts", None)

    # The prompt version no longer matches the stored entry -> cache miss.
    result = before2(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after bumping FUNDAMENTAL_PROMPT_VERSION, "
        "but before-callback returned non-None (spurious cache hit)."
    )
