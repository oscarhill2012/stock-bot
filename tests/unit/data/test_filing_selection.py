"""Tests for ``data.filing_selection.select_current_filings``.

The selector is the single analyst-visibility rule for SEC filings, shared
by the live EDGAR provider and the backtest cache provider so the two paths
cannot drift apart (2026-06-11 filings redesign):

- latest 10-K  with ``filed_at <= as_of``  (the current annual report)
- latest 10-Q  with ``filed_at <= as_of``  (the current quarterly report)
- every 8-K    with ``filed_at`` within ``staleness_days`` of ``as_of``

Periodic forms carry no staleness bound — an eleven-month-old 10-K *is* the
current annual report; "latest one" self-regulates for any compliant filer.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from data.filing_selection import select_current_filings
from data.models import Filing

# A fixed simulation clock for every test — 2026-03-02 16:00 UTC.
AS_OF = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)


def _filing(form_type: str, filed_at: datetime, accession: str = "") -> Filing:
    """Build a minimal ``Filing`` for selector tests.

    Parameters
    ----------
    form_type:
        SEC form type, e.g. ``"10-K"``.
    filed_at:
        Filing timestamp (timezone-aware).
    accession:
        Optional accession number; defaults to a value derived from the
        form type and date so each fixture row is unique.
    """
    return Filing(
        ticker="AAPL",
        form_type=form_type,
        filed_at=filed_at,
        accession_no=accession or f"{form_type}-{filed_at.date().isoformat()}",
    )


# ---------------------------------------------------------------------------
# Periodic forms — latest one per form, older ones superseded
# ---------------------------------------------------------------------------

def test_keeps_only_latest_10k() -> None:
    """Two 10-Ks → only the more recently filed one survives."""
    older  = _filing("10-K", datetime(2025, 1, 31, tzinfo=UTC))
    newer  = _filing("10-K", datetime(2026, 1, 30, tzinfo=UTC))

    out = select_current_filings([older, newer], as_of=AS_OF, staleness_days=90)

    kinds = [(f.form_type, f.filed_at) for f in out]
    assert ("10-K", newer.filed_at) in kinds
    assert ("10-K", older.filed_at) not in kinds


def test_keeps_only_latest_10q() -> None:
    """Three 10-Qs → only the most recent survives."""
    qs = [
        _filing("10-Q", datetime(2025, 5, 2, tzinfo=UTC)),
        _filing("10-Q", datetime(2025, 8, 1, tzinfo=UTC)),
        _filing("10-Q", datetime(2025, 11, 3, tzinfo=UTC)),
    ]

    out = select_current_filings(qs, as_of=AS_OF, staleness_days=90)

    assert [f.filed_at for f in out if f.form_type == "10-Q"] == [qs[2].filed_at]


def test_periodic_forms_have_no_staleness_bound() -> None:
    """An eleven-month-old 10-K is still the current annual report — kept."""
    old_10k = _filing("10-K", datetime(2025, 4, 1, tzinfo=UTC))   # ~11 months before AS_OF

    out = select_current_filings([old_10k], as_of=AS_OF, staleness_days=90)

    assert [f.filed_at for f in out] == [old_10k.filed_at]


# ---------------------------------------------------------------------------
# 8-Ks — event forms decay with the staleness horizon
# ---------------------------------------------------------------------------

def test_8ks_within_staleness_kept_older_dropped() -> None:
    """All 8-Ks inside the horizon survive; anything older is dropped."""
    fresh_1 = _filing("8-K", datetime(2026, 2, 20, tzinfo=UTC))
    fresh_2 = _filing("8-K", datetime(2026, 1, 15, tzinfo=UTC))
    stale   = _filing("8-K", datetime(2025, 11, 1, tzinfo=UTC))   # > 90 days before AS_OF

    out = select_current_filings(
        [fresh_1, fresh_2, stale], as_of=AS_OF, staleness_days=90,
    )

    dates = [f.filed_at for f in out]
    assert fresh_1.filed_at in dates
    assert fresh_2.filed_at in dates
    assert stale.filed_at not in dates


def test_8k_on_staleness_boundary_is_kept() -> None:
    """An 8-K filed exactly ``staleness_days`` before ``as_of`` is inclusive."""
    boundary = _filing("8-K", AS_OF - timedelta(days=90))

    out = select_current_filings([boundary], as_of=AS_OF, staleness_days=90)

    assert [f.filed_at for f in out] == [boundary.filed_at]


# ---------------------------------------------------------------------------
# PIT correctness and hygiene
# ---------------------------------------------------------------------------

def test_filings_after_as_of_are_invisible() -> None:
    """Nothing filed after ``as_of`` may surface — regardless of form type."""
    future_10k = _filing("10-K", datetime(2026, 3, 5, tzinfo=UTC))
    future_8k  = _filing("8-K",  datetime(2026, 3, 3, tzinfo=UTC))
    past_10k   = _filing("10-K", datetime(2026, 1, 30, tzinfo=UTC))

    out = select_current_filings(
        [future_10k, future_8k, past_10k], as_of=AS_OF, staleness_days=90,
    )

    assert [f.filed_at for f in out] == [past_10k.filed_at]


def test_unknown_form_types_are_dropped() -> None:
    """Only 10-K / 10-Q / 8-K are analyst-visible — anything else is filtered."""
    odd = _filing("SC 13G", datetime(2026, 2, 1, tzinfo=UTC))

    out = select_current_filings([odd], as_of=AS_OF, staleness_days=90)

    assert out == []


def test_result_ordered_most_recent_first() -> None:
    """Output ordering matches the read paths: newest ``filed_at`` first."""
    k  = _filing("10-K", datetime(2026, 1, 30, tzinfo=UTC))
    q  = _filing("10-Q", datetime(2025, 11, 3, tzinfo=UTC))
    e1 = _filing("8-K",  datetime(2026, 2, 20, tzinfo=UTC))
    e2 = _filing("8-K",  datetime(2026, 1, 2, tzinfo=UTC))

    out = select_current_filings([q, e1, k, e2], as_of=AS_OF, staleness_days=90)

    assert [f.filed_at for f in out] == sorted(
        (f.filed_at for f in out), reverse=True,
    )
    assert len(out) == 4


def test_empty_input_returns_empty() -> None:
    """No candidates → empty selection (starvation checks live in the audit)."""
    assert select_current_filings([], as_of=AS_OF, staleness_days=90) == []
