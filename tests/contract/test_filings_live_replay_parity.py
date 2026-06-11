"""Contract test: live EDGAR and backtest replay serve identical filing selections.

The 2026-06-11 filings redesign moved the analyst-visibility rule into the
shared ``data.filing_selection.select_current_filings``, applied by both:

- the live EDGAR provider, after its per-form queries;
- the backtest cache provider, after reading every cached row ≤ the tick.

This test is the drift tripwire.  It defines one synthetic *universe* of
filings, exposes it to the live provider through fake EDGAR query seams
(which emulate the SEC's date-bounded query semantics) and to the cache
provider through a seeded store (as a backfill would have written it), then
asserts both paths return the **same accession numbers in the same order**.

If either path grows its own filtering, capping, or ordering quirk, the two
selections diverge and this test fails.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from backtest.providers import _store_handle
from data.models import Filing

# The simulation clock both paths are queried at.
AS_OF = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)

STALENESS_DAYS = 90


@dataclass
class _FakeRawFiling:
    """Stand-in for a raw edgartools filing object (live-path input)."""

    form: str
    filing_date: date
    accession_no: str
    items: str = ""
    body: str = ""

    def text(self) -> str:
        """Return the filing body text."""
        return self.body


# ---------------------------------------------------------------------------
# The shared universe — every filing that "exists" upstream.
#
# Deliberately includes superseded periodic filings, a stale 8-K, and a
# future-dated filing so the selection rule has real work to do on both paths.
# ---------------------------------------------------------------------------

_UNIVERSE: list[_FakeRawFiling] = [
    _FakeRawFiling("10-K",   date(2025, 1, 31), "K-superseded"),
    _FakeRawFiling("10-K",   date(2026, 1, 30), "K-current"),
    _FakeRawFiling("10-K/A", date(2026, 2, 15), "K-amended"),   # amendment — must be excluded
    _FakeRawFiling("10-Q",   date(2025, 8, 1),  "Q-superseded"),
    _FakeRawFiling("10-Q",   date(2025, 11, 3), "Q-current"),
    _FakeRawFiling("8-K",    date(2026, 2, 20), "E-fresh"),
    _FakeRawFiling("8-K",    date(2025, 10, 7), "E-stale"),     # outside the 90-day horizon
    _FakeRawFiling("8-K",    date(2026, 3, 10), "E-future"),    # filed after AS_OF
    _FakeRawFiling("8-K/A",  date(2026, 2, 21), "E-amended"),   # amendment — must be excluded
]


def _universe_as_models() -> list[Filing]:
    """Convert the raw universe into ``Filing`` models, as a backfill writes them.

    Returns
    -------
    list[Filing]
        One ``Filing`` per universe row, ``filed_at`` at midnight UTC of the
        filing date — matching how the live provider coerces edgartools
        filing dates, so ordering keys are identical on both paths.
    """
    return [
        Filing(
            ticker="AAPL",
            form_type=raw.form,
            filed_at=datetime.combine(raw.filing_date, datetime.min.time(), tzinfo=UTC),
            accession_no=raw.accession_no,
        )
        for raw in _UNIVERSE
    ]


# ---------------------------------------------------------------------------
# Fake EDGAR query seams — emulate the SEC's date-bounded query semantics
# over the universe, exactly as the real ``Company.get_filings`` calls would.
# ---------------------------------------------------------------------------

def _fake_latest(symbol: str, form: str, as_of: datetime) -> list[_FakeRawFiling]:
    """Return the single newest ``form`` filing on or before ``as_of``."""
    candidates = [
        raw for raw in _UNIVERSE
        if raw.form == form and raw.filing_date <= as_of.date()
    ]
    candidates.sort(key=lambda raw: raw.filing_date, reverse=True)
    return candidates[:1]


def _fake_range(
    symbol: str,
    forms: tuple[str, ...],
    lower: datetime,
    upper: datetime,
) -> list[_FakeRawFiling]:
    """Return every filing of ``forms`` filed within ``[lower, upper]``."""
    return [
        raw for raw in _UNIVERSE
        if raw.form in forms and lower.date() <= raw.filing_date <= upper.date()
    ]


@pytest.mark.asyncio
async def test_live_and_replay_serve_identical_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both read paths must surface the same accessions in the same order."""
    # ── Live path: EDGAR provider over the fake query seams ────────────────
    import data.providers.filings.edgar as edgar_mod

    monkeypatch.setattr(edgar_mod, "_iter_latest_filing",  _fake_latest)
    monkeypatch.setattr(edgar_mod, "_iter_filings_range",  _fake_range)

    live = await edgar_mod.fetch(
        "AAPL",
        as_of=AS_OF,
        staleness_days=STALENESS_DAYS,
        include_excerpts=False,
    )

    # ── Replay path: cache provider over a store seeded with the universe ──
    store = CachedDataStore(tmp_path / "store.sqlite")
    _store_handle.set_store(store)
    try:
        from backtest.providers import filings_cache

        store.write_filings("AAPL", _universe_as_models())

        replay = await filings_cache.fetch(
            "AAPL", as_of=AS_OF, staleness_days=STALENESS_DAYS,
        )
    finally:
        _store_handle.clear_store()

    # ── Parity: same filings, same order ───────────────────────────────────
    assert [f.accession_no for f in live] == [f.accession_no for f in replay]

    # Sanity-pin the expected selection so a both-paths-wrong bug (e.g. the
    # selector itself regressing) cannot slip through as "still equal".
    assert {f.accession_no for f in live} == {"K-current", "Q-current", "E-fresh"}
