"""Tests for the fetch-phase similarity precompute pass."""
from __future__ import annotations

from datetime import datetime

from agents.analysts.fundamental.fetch import compute_filing_similarities
from data.models import Filing

# Full-length MD&A prose (> 400 chars — the stub threshold) reused across the
# "real prose" fixtures below, so pairing/scoring tests exercise genuine
# similarity scoring rather than tripping the stub guard by accident. Kept
# well over the 400-char threshold with margin, so future threshold tuning
# does not flip these fixtures into "stub" territory.
_FULL_PRIOR_MDA = (
    "Revenue was 12.1 billion this quarter with strong demand across every "
    "reportable segment, driven by continued expansion in our core markets "
    "and disciplined cost management throughout the period, partially "
    "offset by adverse currency translation effects in our international "
    "operations and modestly higher input costs across the supply chain, "
    "as well as incremental investment in research and development intended "
    "to sustain our competitive position over the medium term."
)
_FULL_CURRENT_MDA = (
    "Revenue was 13.4 billion this quarter with strong demand across every "
    "reportable segment, driven by continued expansion in our core markets "
    "and disciplined cost management throughout the period, partially "
    "offset by adverse currency translation effects in our international "
    "operations and modestly higher input costs across the supply chain, "
    "as well as incremental investment in research and development intended "
    "to sustain our competitive position over the medium term."
)

# Short incorporation-by-reference stub (< 400 chars) — mirrors real EDGAR
# cross-reference filings (e.g. DGX / CMS / ATO) where Item 7 merely points
# elsewhere instead of containing real prose.
_STUB_MDA = "Item 7. MD&A is incorporated by reference. See page 59."


def _q(accession: str, period: str, filed: datetime, mda: str) -> Filing:
    """Build a minimal 10-Q ``Filing`` for ``AAPL`` carrying the given MD&A text.

    Parameters
    ----------
    accession:
        Accession number (identifies the filing within the pool).
    period:
        ``period_of_report`` ISO date driving prior-year pairing.
    filed:
        ``filed_at`` timestamp.
    mda:
        MD&A excerpt text to score.

    Returns
    -------
    Filing
        A 10-Q filing for ticker ``AAPL``.
    """
    return Filing(
        ticker="AAPL", form_type="10-Q", accession_no=accession,
        filed_at=filed, period_of_report=period, mda_excerpt=mda,
    )


def test_pairs_same_quarter_prior_year_and_populates_cosine() -> None:
    """A 10-Q must pair with the same-quarter prior-year 10-Q and get a cosine.

    Both sides are deliberately full-length (> 400 chars, the stub threshold)
    so this exercises pairing + scoring, not the stub guard (see the
    dedicated stub-guard tests below).
    """
    prior   = _q("p", "2023-06-30", datetime(2023, 8, 1), _FULL_PRIOR_MDA)
    current = _q("c", "2024-06-30", datetime(2024, 8, 1), _FULL_CURRENT_MDA)

    out = {f.accession_no: f for f in compute_filing_similarities([current, prior])}

    assert out["c"].mda_cosine_vs_prior is not None
    assert out["c"].mda_cosine_vs_prior > 0.9   # number-only change => near-verbatim
    assert out["p"].mda_cosine_vs_prior is None  # no prior pair for the oldest


def test_unpaired_filing_leaves_scalars_none() -> None:
    """A filing with no prior-year pair keeps None scalars (correct absence)."""
    lone = _q("x", "2024-06-30", datetime(2024, 8, 1), "Some MD&A prose here.")
    out = compute_filing_similarities([lone])
    assert out[0].mda_cosine_vs_prior is None


def test_stub_prior_text_yields_no_cosine_not_a_spurious_score() -> None:
    """A stub prior-year MD&A (< 400 chars) must NOT be scored.

    Mirrors the ATO case: the current filing's MD&A is full prose, but its
    same-quarter prior-year pair is an incorporation-by-reference stub. The
    precompute must guard this out (``mda_cosine_vs_prior is None``), the
    same way ``_render_diffed_section`` already refuses to diff it — a
    spurious full-vs-stub cosine would poison the self-relative history
    baseline other filings' scale lines rank against.
    """
    stub_prior   = _q("p", "2023-06-30", datetime(2023, 8, 1), _STUB_MDA)
    full_current = _q("c", "2024-06-30", datetime(2024, 8, 1), _FULL_CURRENT_MDA)

    out = {f.accession_no: f for f in compute_filing_similarities([full_current, stub_prior])}

    assert out["c"].mda_cosine_vs_prior is None  # guarded out, not a spurious score


def test_two_full_text_sections_still_score() -> None:
    """The inverse of the stub guard: two full-text sections still produce a cosine."""
    prior   = _q("p", "2023-06-30", datetime(2023, 8, 1), _FULL_PRIOR_MDA)
    current = _q("c", "2024-06-30", datetime(2024, 8, 1), _FULL_CURRENT_MDA)

    out = {f.accession_no: f for f in compute_filing_similarities([current, prior])}

    assert out["c"].mda_cosine_vs_prior is not None
    assert out["c"].mda_cosine_vs_prior > 0.9   # number-only change => near-verbatim


def test_recompute_clears_stale_stub_cosine_to_none() -> None:
    """A recompute must overwrite (not merely leave alone) a stale stub cosine.

    Simulates the operator recompute scenario: a filing whose CURRENT text is
    now a stub, but whose input dict already carries a stale persisted cosine
    from an earlier, un-guarded run (e.g. 0.99 from a full-vs-stub score).
    The precompute output must be authoritative — the stale value must be
    cleared to ``None``, not silently carried through.

    Without the "always set all six fields in ``updates``" fix this test
    fails: the guarded-out section would be absent from ``updates``, so
    ``model_copy(update=updates)`` would leave the stale 0.99 in place.
    """
    prior = _q("p", "2023-06-30", datetime(2023, 8, 1), _FULL_PRIOR_MDA)

    # Current filing's MD&A is now a stub, but it carries a stale cosine from
    # a prior (un-guarded) precompute run.
    stale_current = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="c",
        filed_at=datetime(2024, 8, 1), period_of_report="2024-06-30",
        mda_excerpt=_STUB_MDA,
        mda_cosine_vs_prior=0.99, mda_jaccard_vs_prior=0.98,
    )

    out = {f.accession_no: f for f in compute_filing_similarities([stale_current, prior])}

    assert out["c"].mda_cosine_vs_prior is None
    assert out["c"].mda_jaccard_vs_prior is None
