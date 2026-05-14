"""Integration test: bumping FUNDAMENTAL_PROMPT_VERSION invalidates the Fundamental cache.

Same harness as ``test_fundamental_cache_roundtrip``, but between the first and
second runs the prompt-version string is changed.  The second ``before`` call
must return ``None`` (cache miss) because the stored prompt version no longer
matches.

This mirrors ``test_news_cache_prompt_version`` for the Fundamental analyst.

The ``cache_root`` fixture and ``_Ctx`` stub are defined in ``conftest.py``
and auto-discovered by pytest.

NOTE: the ``_after`` hook now reads verdicts from ``llm_response.content``
directly (B22 bug-fix).  Tests must pass a fake response object.

NOTE: because ``prompt_version`` is now a plain string argument captured at
factory-invocation time, the test passes the "bumped" version directly to a
second ``make_report_cache_callbacks`` call rather than monkeypatching the
module attribute.
"""
from __future__ import annotations

import json
import types

from agents.analysts.cache_callbacks import make_report_cache_callbacks
from agents.analysts.fundamental.agent import _fundamental_hash_inputs_from_dict
from agents.analysts.report_cache import FUNDAMENTAL_PROMPT_VERSION
from tests.integration.conftest import _Ctx


def _fake_llm_response(verdicts: list[dict]) -> types.SimpleNamespace:
    """Minimal fake LLM response whose ``.content.parts[0].text`` is the JSON payload."""
    text    = json.dumps({"verdicts": verdicts})
    part    = types.SimpleNamespace(text=text)
    content = types.SimpleNamespace(parts=[part])
    return types.SimpleNamespace(content=content)


def _make_fundamental_callbacks(prompt_version: str = FUNDAMENTAL_PROMPT_VERSION):
    """Construct fundamental cache callbacks via the shared factory.

    Parameters
    ----------
    prompt_version:
        Prompt-version string to bake into the callbacks.  Defaults to the
        real constant; pass a different string to simulate a version bump.
    """
    return make_report_cache_callbacks(
        analyst_name       = "fundamental",
        prompt_version     = prompt_version,
        data_state_key     = "fundamental_data",
        verdicts_state_key = "fundamental_verdicts",
        hash_inputs        = lambda d: _fundamental_hash_inputs_from_dict(
            ticker=((d or {}).get("ratios") or {}).get("ticker", ""),
            triad=(d or {}),
        ),
        trace_label        = "03_fundamental_llm",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_prompt_version_bump_busts_cache(cache_root):
    """Bumping the prompt version must produce a cache miss on the 2nd run.

    The cache entry written by the first run contains the original prompt version.
    The second pair of callbacks is built with a different version string, so
    the stored entry no longer matches -> miss.
    """
    before, after = _make_fundamental_callbacks(FUNDAMENTAL_PROMPT_VERSION)

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

    verdicts_payload = [{
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
    }]

    after(ctx, llm_response=_fake_llm_response(verdicts_payload))

    # --- Rebuild with a bumped prompt version (simulates a real version bump) ---
    before2, _ = _make_fundamental_callbacks("v-test-2")

    ctx.state.pop("fundamental_verdicts", None)

    # The prompt version no longer matches the stored entry -> cache miss.
    result = before2(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after bumping FUNDAMENTAL_PROMPT_VERSION, "
        "but before-callback returned non-None (spurious cache hit)."
    )
