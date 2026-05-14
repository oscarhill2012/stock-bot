"""Integration test: adding a new Filing invalidates the Fundamental cache.

Same harness as ``test_fundamental_cache_roundtrip``, but between the first and
second runs a new ``Filing`` is appended to
``state["fundamental_data"]["AAPL"]["filings"]``.  The second ``before`` call
must return ``None`` (cache miss) because the input hash has changed — the
cache is keyed on filing accession numbers, so adding any new filing busts it.

The ``cache_root`` fixture and ``_Ctx`` stub are defined in ``conftest.py``
and auto-discovered by pytest.

NOTE: the ``_after`` hook now reads verdicts from ``llm_response.content``
directly (B22 bug-fix).  Tests must pass a fake response object.
"""
from __future__ import annotations

import json
import types
from datetime import UTC, datetime

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


def _make_fundamental_callbacks():
    """Construct fundamental cache callbacks via the shared factory."""
    return make_report_cache_callbacks(
        analyst_name       = "fundamental",
        prompt_version     = FUNDAMENTAL_PROMPT_VERSION,
        data_state_key     = "fundamental_data",
        verdicts_state_key = "fundamental_verdicts",
        hash_inputs        = lambda d: _fundamental_hash_inputs_from_dict(
            ticker=((d or {}).get("ratios") or {}).get("ticker", ""),
            triad=(d or {}),
        ),
        trace_label        = "03_fundamental_llm",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_filing(accession_no: str) -> dict:
    """Return a minimal Filing dict satisfying all required fields.

    Parameters
    ----------
    accession_no:
        The unique SEC accession number for this filing. The Fundamental hash
        function keys on accession numbers, so changing this value busts the
        cache.

    Returns
    -------
    dict
        A ``Filing.model_dump()``-compatible dict with all required fields.
    """
    return {
        "ticker":      "AAPL",
        "form_type":   "10-Q",
        "filed_at":    datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC).isoformat(),
        "accession_no": accession_no,
        "url":         f"https://sec.gov/Archives/{accession_no}.htm",
        # Optional fields — left as None to keep the fixture lean.
        "mda_excerpt":           None,
        "risk_factors_excerpt":  None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_new_filing_busts_cache(cache_root):
    """Adding a new Filing between runs must produce a cache miss on the 2nd run.

    The Fundamental hash function is sensitive to the set of filing accession
    numbers: a new accession number changes the digest, which invalidates the
    previously cached entry.
    """
    before, after = _make_fundamental_callbacks()

    # --- First run — one existing filing ---
    filing_v1 = _minimal_filing("0000320193-26-000010")

    ctx = _Ctx({
        "tickers": ["AAPL"],
        "fundamental_data": {
            "AAPL": {
                "ratios":  {"ticker": "AAPL", "trailing_pe": 28.5},
                "filings": [filing_v1],
                "insider": None,
            }
        },
    })

    # First run: miss -> LLM would run.
    assert before(ctx, llm_request=None) is None

    # Persist to cache via the fake LLM response.
    verdicts_payload = [{
        "ticker":       "AAPL",
        "lean":         "bullish",
        "magnitude":    0.55,
        "confidence":   0.75,
        "rationale":    "Strong quarterly results.",
        "key_factors":  [],
        "is_no_data":   False,
        "report": {
            "summary": "Quarterly filing shows continued top-line growth.",
            "drivers": [
                {"name": "Revenue beat",       "direction": "bull",    "weight": 0.6, "body": "Revenue exceeded consensus by 4%."},
                {"name": "PE still stretched", "direction": "neutral", "weight": 0.4, "body": "Trailing PE of 28 is above the sector average."},
            ],
        },
    }]

    after(ctx, llm_response=_fake_llm_response(verdicts_payload))

    # --- Second run — add a brand-new filing ---
    filing_v2 = _minimal_filing("0000320193-26-000099")

    # Update state to include the new filing alongside the original.
    ctx.state["fundamental_data"]["AAPL"]["filings"] = [filing_v1, filing_v2]
    ctx.state.pop("fundamental_verdicts", None)

    # The input hash has changed (new accession number) -> must be a cache miss.
    result = before(ctx, llm_request=None)
    assert result is None, (
        "Expected cache miss after adding a new Filing, "
        "but before-callback returned non-None (spurious cache hit)."
    )
