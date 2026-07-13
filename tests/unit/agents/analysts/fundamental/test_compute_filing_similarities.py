"""Tests for the fetch-phase similarity precompute pass."""
from __future__ import annotations

from datetime import datetime

from agents.analysts.fundamental.fetch import compute_filing_similarities
from data.models import Filing


def _q(accession: str, period: str, filed: datetime, mda: str) -> Filing:
    return Filing(
        ticker="AAPL", form_type="10-Q", accession_no=accession,
        filed_at=filed, period_of_report=period, mda_excerpt=mda,
    )


def test_pairs_same_quarter_prior_year_and_populates_cosine() -> None:
    """A 10-Q must pair with the same-quarter prior-year 10-Q and get a cosine."""
    prior = _q("p", "2023-06-30", datetime(2023, 8, 1),
               "Revenue was 12.1 billion this quarter with strong demand.")
    current = _q("c", "2024-06-30", datetime(2024, 8, 1),
                 "Revenue was 13.4 billion this quarter with strong demand.")

    out = {f.accession_no: f for f in compute_filing_similarities([current, prior])}

    assert out["c"].mda_cosine_vs_prior is not None
    assert out["c"].mda_cosine_vs_prior > 0.9   # number-only change => near-verbatim
    assert out["p"].mda_cosine_vs_prior is None  # no prior pair for the oldest


def test_unpaired_filing_leaves_scalars_none() -> None:
    """A filing with no prior-year pair keeps None scalars (correct absence)."""
    lone = _q("x", "2024-06-30", datetime(2024, 8, 1), "Some MD&A prose here.")
    out = compute_filing_similarities([lone])
    assert out[0].mda_cosine_vs_prior is None
