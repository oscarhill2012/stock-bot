"""Integration test: Fundamental analyst is short-circuited by the report cache on a 2nd run.

Uses the cache-callback harness directly rather than running the full ADK pipeline
— far more hermetic and avoids any live LLM calls or network traffic.

The Fundamental cache is more complex than the News cache: the ``_before`` hook
reconstructs typed ``CompanyRatios``, ``list[Filing]``, and ``Form4Bundle``
objects from the dicts stored in ``state["fundamental_data"]`` before computing
the input hash.  This test verifies the full roundtrip: miss -> persist ->
identical-inputs hit.

The ``cache_root`` fixture is defined in ``conftest.py`` and redirects
``get_analysts_config()`` to a tmp_path so the test never touches the real
``cache/reports`` directory.  ``_Ctx`` is also imported from there.
"""
from __future__ import annotations

from agents.analysts.fundamental.agent import _build_fundamental_cache_callbacks
from tests.integration.conftest import _Ctx

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_second_run_hits_cache(cache_root):
    """Identical fundamental inputs on two consecutive runs -> 2nd run short-circuits the LLM.

    Sequence:
    1. Build a state dict with minimal ``CompanyRatios`` (as a model_dump dict),
       an empty filings list, and ``None`` for the insider bundle.
    2. First ``before`` call returns ``None`` (cache miss) — LLM would run.
    3. Simulate the LLM writing verdicts to ``state["fundamental_verdicts"]``.
    4. ``after`` call persists the verdicts to disk.
    5. Clear the verdict key and call ``before`` again with identical inputs.
    6. Assert the second ``before`` call returns non-None (cache hit) and that
       the cached verdict was written back into ``state["fundamental_verdicts"]``.
    """
    before, after = _build_fundamental_cache_callbacks()

    # Minimal state dict mirroring what the fetch callback produces.
    # ``ratios`` is a CompanyRatios.model_dump() dict; ``filings`` is an empty
    # list; ``insider`` is None (the _before hook defaults it to an empty
    # Form4Bundle when falsy).
    ctx = _Ctx({
        "tickers": ["AAPL"],
        "fundamental_data": {
            "AAPL": {
                "ratios":  {"ticker": "AAPL", "trailing_pe": 36.0},
                "filings": [],
                "insider": None,
            }
        },
    })

    # --- First run — cache miss ---
    # The before-callback must return None to allow the LLM to run.
    assert before(ctx, llm_request=None) is None

    # Simulate the LLM having run and written verdicts into state.
    # direction uses "bull"/"bear"/"neutral" per ReportDriver schema.
    # lean uses "bullish"/"bearish"/"neutral" per AnalystVerdict schema.
    ctx.state["fundamental_verdicts"] = {
        "verdicts": [{
            "ticker":       "AAPL",
            "lean":         "neutral",
            "magnitude":    0.3,
            "confidence":   0.7,
            "rationale":    "Stable but unexciting ratios.",
            "key_factors":  [],
            "is_no_data":   False,
            "report": {
                "summary": "Fundamentals look broadly stable.",
                "drivers": [
                    {"name": "PE in line with sector",   "direction": "neutral", "weight": 0.5, "body": "Trailing PE of 36 is at the sector median."},
                    {"name": "No recent insider selling", "direction": "bull",    "weight": 0.5, "body": "No insider trades filed in the last 90 days."},
                ],
            },
        }],
    }

    # after-callback persists the verdict to disk — returns None.
    after(ctx, llm_response=None)

    # --- Second run with identical inputs — cache hit ---
    # Remove the verdicts key so we can confirm the cache restores it.
    ctx.state.pop("fundamental_verdicts", None)

    short_circuit = before(ctx, llm_request=None)

    # The before-callback must return a non-None Content object on a hit.
    assert short_circuit is not None, (
        "Expected a cache hit on the second run with identical inputs, "
        "but before-callback returned None (unexpected cache miss)."
    )

    # The cached verdicts must have been written back into state.
    assert "fundamental_verdicts" in ctx.state, (
        "Cache hit did not populate state['fundamental_verdicts']."
    )
    assert ctx.state["fundamental_verdicts"]["verdicts"][0]["ticker"] == "AAPL", (
        "Cached verdict ticker does not match expected value 'AAPL'."
    )
