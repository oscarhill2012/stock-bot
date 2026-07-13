"""Unit tests for the self-relative scale-line injection in ``_build_ticker_context``.

Task 7 wires ``build_scale_summary`` (Task 6) into ``_render_diffed_section``
so that each diffed prose section carries a "   scale: <summary>" line built
from the firm's OWN persisted cosine history in the as_of-sliced baseline
pool.  This complements the paragraph-level diff: the diff shows *what*
changed, the scale line tells the LLM whether the *amount* of change is
unusual for this specific firm.
"""
from __future__ import annotations

from agents.analysts.fundamental.fetch import _build_ticker_context
from data.models import Form4Bundle


def test_context_carries_self_relative_scale_line() -> None:
    """The MD&A section must carry a self-relative scale line built from the
    firm's own persisted cosine history in the baseline pool."""
    current = {
        "form_type": "10-Q", "accession_no": "c", "filed_at": "2024-08-01",
        "period_of_report": "2024-06-30",
        "mda_excerpt": "A newly disclosed regulatory probe may materially harm results. " * 20,
        "mda_cosine_vs_prior": 0.40,
    }
    # Four years of prior-year cosines for this firm's 10-Q MD&A.
    history = [
        {"form_type": "10-Q", "accession_no": f"h{i}", "period_of_report": p,
         "mda_excerpt": "boilerplate " * 80, "mda_cosine_vs_prior": c}
        for i, (p, c) in enumerate(
            [("2020-06-30", 0.90), ("2021-06-30", 0.92),
             ("2022-06-30", 0.88), ("2023-06-30", 0.91)]
        )
    ]

    block = _build_ticker_context(
        "AAPL", [current], Form4Bundle(trades=[], derivatives=[]),
        insider_lookback_days=30, ratios=None,
        baseline_filings_payload=history,
    )
    assert "scale:" in block
    # The scale summariser emits UPPERCASE band verdicts ("changed MORE than
    # usual for this firm") — the assertion is deliberately case-insensitive
    # (settled ruling; do not lowercase the summariser output to "fix" this).
    assert "more than usual" in block.lower()
