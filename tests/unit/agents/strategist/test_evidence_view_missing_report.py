"""D1.3 — strategist evidence renders a visibility placeholder when report=None.

The schema validator (D1.1) closes the loophole for normal flow; this
test pins the defence-in-depth layer.  If a future regression somehow
re-introduces ``report=None`` on a ``is_no_data=False`` verdict, the
strategist sees the absence as data rather than silently reasoning over
less evidence.
"""
from __future__ import annotations

from datetime import UTC, datetime

from agents.strategist.evidence_view import _format_per_analyst
from contract.evidence import AnalystEvidence, AnalystVerdict
from contract.ticker_evidence import AggregateVerdict, TickerEvidence


def _verdict(*, is_no_data: bool, report=None) -> AnalystVerdict:
    return AnalystVerdict.model_construct(
        # ``model_construct`` skips the new D1.1 validator so we can
        # construct the degenerate (is_no_data=False, report=None)
        # combination for this defence-in-depth test specifically.
        lean        = "bullish",
        magnitude   = 0.5,
        confidence  = 0.6,
        rationale   = "x",
        key_factors = [],
        is_no_data  = is_no_data,
        report      = report,
    )


def test_missing_report_renders_placeholder() -> None:
    """A non-no-data verdict with report=None renders the placeholder."""

    # ``model_construct`` is used all the way up the chain so that Pydantic
    # never re-runs the D1.1 validator on the degenerate verdict — the
    # validator lives on AnalystVerdict, and AnalystEvidence re-validates
    # the verdict field during normal construction.
    evidence = AnalystEvidence.model_construct(
        ticker      = "AAPL",
        analyst     = "news",
        tick_id     = "tick_X",
        recorded_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        verdict     = _verdict(is_no_data=False, report=None),
        features    = {},
        feature_warnings = [],
        raw_text    = None,
    )

    te = TickerEvidence.model_construct(
        ticker      = "AAPL",
        tick_id     = "tick_X",
        recorded_at = datetime(2026, 5, 22, 12, 0, tzinfo=UTC),
        per_analyst = {"news": evidence},
        aggregate   = AggregateVerdict(
            lean         = "bullish",
            magnitude    = 0.5,
            confidence   = 0.6,
            disagreement = 0.0,
        ),
        weights     = {"news": 1.0},
    )

    lines = _format_per_analyst(te)
    joined = "\n".join(lines)

    assert "(no report this tick — analyst compliance failure)" in joined
