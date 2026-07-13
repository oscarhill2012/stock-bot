"""Tests for filing_diff — similarity-threshold dedup + numeric-delta surfacing."""
from __future__ import annotations

from agents.analysts.fundamental.filing_diff import (
    FILING_DIFF_ALGO_VERSION,
    filing_diff,
)

_DEDUP = 0.92
_NUM_PCT = 0.10


def _run(current: str, prior: str):
    return filing_diff(
        current, prior,
        dedup_cosine=_DEDUP,
        numeric_delta_pct=_NUM_PCT,
        algo_version=FILING_DIFF_ALGO_VERSION,
        prior_period_label="FY2023",
    )


def test_number_only_rollforward_dedups_to_near_verbatim() -> None:
    """A filing that only rolls numbers forward must read as near-verbatim.

    This is the quiet-bullish leg Plan 1 could never reach.  Every paragraph is
    a numeric roll-forward of the prior year, so all should dedup and the
    near-verbatim marker must fire with NO body prose.
    """
    prior = (
        "Revenue was 12.1 billion, up 4% year over year.\n\n"
        "Operating margin improved to 21.0% from 19.5%."
    )
    current = (
        "Revenue was 13.4 billion, up 9% year over year.\n\n"
        "Operating margin improved to 23.0% from 21.0%."
    )
    text, stats = _run(current, prior)
    assert "near-verbatim" in text
    assert stats["paragraphs_dropped"] == stats["paragraphs_total"]


def test_numeric_delta_is_surfaced_even_when_paragraph_deduped() -> None:
    """A large figure change inside a deduped paragraph must be surfaced.

    Number normalisation hides it from the similarity view; the numeric-delta
    detector must bring it back so the LLM can weigh it.
    """
    prior   = "Total contractual obligations were 1.0 billion at year end."
    current = "Total contractual obligations were 3.0 billion at year end."
    text, stats = _run(current, prior)
    assert stats["numeric_deltas"], "a >=10% figure change must be recorded"
    assert "NUMERIC DELTAS" in text


def test_genuine_rewrite_survives_the_diff() -> None:
    """Substantively new prose must NOT be deduped — it is the bearish signal."""
    prior   = "We expect continued strong demand across our core markets."
    current = "A newly disclosed regulatory investigation may materially harm results."
    text, stats = _run(current, prior)
    assert stats["paragraphs_dropped"] == 0
    assert "regulatory investigation" in text


def test_single_newline_document_is_chunked_via_fallback() -> None:
    """A document with only single-newline breaks must still split into paragraphs.

    ``_split_paragraphs`` falls back to single-newline splitting when the text
    has no blank-line boundaries (common in some EDGAR extracts).  Using
    genuinely different, non-numeric prose per line so nothing dedups, this pins
    that the fallback chunks the document into multiple paragraphs rather than
    treating the whole thing as one blob.
    """
    prior = (
        "We expect continued strong demand across our core markets.\n"
        "Our supply chain remained resilient throughout the period."
    )
    current = (
        "A newly disclosed regulatory investigation may materially harm results.\n"
        "An unforeseen factory closure disrupted deliveries to key customers."
    )
    text, stats = _run(current, prior)

    # The fallback must have produced more than one paragraph from the
    # single-newline document — not collapsed it into a single blob.
    assert stats["paragraphs_total"] > 1

    # None of the substantively-new prose dedups, so both survivors render.
    assert stats["paragraphs_dropped"] == 0
    assert "regulatory investigation" in text
    assert "factory closure" in text
