"""Regression tests for prior-year baseline pairing date parsing.

The MD&A de-boilerplate feature (Phase 13) pairs a current filing with its
prior-year counterpart by parsing ``period_of_report``.  The live EDGAR
provider emits this field in compact ``YYYYMMDD`` form, but the golden cache
that backs the backtest stores it in ISO ``YYYY-MM-DD`` form.

The original implementation parsed only ``YYYYMMDD``, so every cache-backed
(backtest) pairing raised ``ValueError``, was silently swallowed, and fell
back to full-filing text — disabling de-boilerplate for the entire run and
roughly doubling fundamental token usage.

These tests pin both formats and assert that a genuinely unparseable period
fails *loudly* rather than silently.
"""
from __future__ import annotations

import logging

from agents.analysts.fundamental.fetch import _find_prior_year_baseline


def _baseline(form_type: str, period: str, accession: str) -> dict:
    """Build a minimal baseline filing dict for the pairing helper.

    Parameters
    ----------
    form_type:
        Filing form type (e.g. ``"10-Q"``).
    period:
        ``period_of_report`` string in whichever format under test.
    accession:
        Accession identifier, used by the assertions to confirm which
        candidate was selected.

    Returns
    -------
    dict
        A filing dict with just the fields the pairing helper reads.
    """
    return {
        "form_type": form_type,
        "period_of_report": period,
        "accession_no": accession,
    }


def test_pairs_prior_year_baseline_with_iso_period_of_report() -> None:
    """ISO ``YYYY-MM-DD`` periods (golden cache shape) must pair correctly.

    This is the backtest path that the original ``%Y%m%d``-only parser broke.
    A 10-Q dated 2025-12-27 must pair with the 10-Q dated 2024-12-28 (364 days
    earlier — inside the 335–395 day window), not the prior-quarter filing and
    not the wrong form type.
    """
    current_period = "2025-12-27"  # ISO, exactly as the golden cache stores it.

    baselines = [
        _baseline("10-Q", "2024-12-28", "prior-year"),     # ~364 days — the match.
        _baseline("10-Q", "2025-09-27", "prior-quarter"),  # ~91 days — too recent.
        _baseline("10-K", "2024-12-28", "wrong-form"),     # right date, wrong form.
    ]

    match = _find_prior_year_baseline(current_period, "10-Q", baselines)

    assert match is not None, "ISO-format period_of_report should pair, not fall back"
    assert match["accession_no"] == "prior-year"


def test_pairs_prior_year_baseline_with_compact_period_of_report() -> None:
    """Compact ``YYYYMMDD`` periods (live EDGAR shape) must still pair.

    Guards against a fix that swaps one rigid format for another — both
    provider shapes must work so live and backtest behave identically.
    """
    current_period = "20251227"

    baselines = [
        _baseline("10-Q", "20241228", "prior-year"),
        _baseline("10-Q", "20250927", "prior-quarter"),
    ]

    match = _find_prior_year_baseline(current_period, "10-Q", baselines)

    assert match is not None
    assert match["accession_no"] == "prior-year"


def test_pairs_across_mixed_format_current_and_baseline() -> None:
    """A current ISO period must pair against a compact baseline (and vice versa).

    The two provider paths can interleave (e.g. a cache row paired against a
    freshly fetched baseline), so the parser must normalise each side
    independently rather than assuming both share a format.
    """
    match = _find_prior_year_baseline(
        "2025-12-27",                                  # ISO current
        "10-Q",
        [_baseline("10-Q", "20241228", "prior-year")],  # compact baseline
    )

    assert match is not None
    assert match["accession_no"] == "prior-year"


def test_unparseable_current_period_logs_a_warning(caplog) -> None:
    """A non-empty period that matches no known format must fail loudly.

    The original silent ``except: return None`` is exactly the failure class
    that hid this bug for an entire run.  A genuinely malformed period is rare
    and worth a warning — silence must never again mask a disabled feature.
    """
    baselines = [_baseline("10-Q", "2024-12-28", "prior-year")]

    with caplog.at_level(logging.WARNING, logger="agents.analysts.fundamental.fetch"):
        match = _find_prior_year_baseline("not-a-date", "10-Q", baselines)

    assert match is None
    assert any(
        "not-a-date" in record.getMessage()
        for record in caplog.records
    ), "an unparseable period_of_report should emit a warning naming the bad value"
