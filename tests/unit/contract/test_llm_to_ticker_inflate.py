# tests/unit/contract/test_llm_to_ticker_inflate.py
"""Identity-of-inflate: the LLM→canonical conversion lives in one place."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from contract.evidence import (
    AnalystReport,
    LlmTickerVerdict,
    ReportDriver,
    TickerVerdict,
)


def _make_llm_verdict(*, is_no_data: bool = False, report=None) -> LlmTickerVerdict:
    """Build a minimal valid LlmTickerVerdict for inflate testing."""

    if report is None:
        report = AnalystReport(
            summary="A short gestalt sentence describing the lean.",
            drivers=[
                ReportDriver(name="catalyst", direction="bull", weight=0.6, body="x"),
                ReportDriver(name="risk",     direction="bear", weight=0.4, body="y"),
            ],
        )
    return LlmTickerVerdict(
        ticker      = "AAPL",
        lean        = "bullish",
        magnitude   = 0.5,
        confidence  = 0.5,
        is_no_data  = is_no_data,
        key_factors = ["x"],
        report      = report,
    )


def test_to_ticker_verdict_returns_canonical_shape():
    """LlmTickerVerdict.to_ticker_verdict yields a TickerVerdict with the same ticker."""

    llm = _make_llm_verdict()
    canonical = llm.to_ticker_verdict()

    assert isinstance(canonical, TickerVerdict)
    assert canonical.ticker     == "AAPL"
    assert canonical.lean       == "bullish"
    assert canonical.rationale  == ""              # downstream default — LLM no longer emits it
    assert canonical.report is not None
    assert canonical.is_no_data is False


def test_to_ticker_verdict_propagates_is_no_data_branch():
    """A no-data LLM emit converts cleanly to a TickerVerdict without tripping the prose-surface validator.

    On the LLM emit-schema, ``report`` is always required — the LLM must supply
    it even for a no-data emit (a short "no data" summary satisfies the schema).
    This test confirms that:
    - the converted object is a ``TickerVerdict`` instance,
    - the ``is_no_data`` flag is carried across faithfully,
    - ``report`` and its ``summary`` field survive the conversion intact
      (so the no-data branch genuinely exercises field propagation, not just
      the two Boolean flags), and
    - the ``_prose_surface_required_when_data_present`` validator does not fire
      (``is_no_data=True`` short-circuits it).
    """

    llm = _make_llm_verdict(is_no_data=True)
    canonical = llm.to_ticker_verdict()

    assert isinstance(canonical, TickerVerdict)
    assert canonical.is_no_data is True
    assert canonical.report is not None
    assert canonical.report.summary == llm.report.summary


def test_inflate_does_not_silently_drop_fields():
    """Round-trip via model_dump produces no field drift between LLM and canonical."""

    llm = _make_llm_verdict()
    canonical = llm.to_ticker_verdict()
    dumped = canonical.model_dump()

    # The five LLM-emitted scalar fields must match by value.
    for k in ("ticker", "lean", "magnitude", "confidence", "is_no_data"):
        assert dumped[k] == getattr(llm, k)
    assert dumped["key_factors"] == llm.key_factors
    assert dumped["report"]["summary"] == llm.report.summary
