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
    period_of_report: str = ""   # YYYYMMDD; edgar._build_filing reads this attr

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


# ---------------------------------------------------------------------------
# Pool-mode parity (Phase 13 de-boilerplate baseline supply)
#
# The de-boilerplate assembly issues a *pool*-mode provider call (``from_date``
# given) to obtain the prior-year periodic pool.  The pairing layer then picks
# the same-period-prior-year filing out of that pool to diff against.  This
# test pins that BOTH read paths hand the pairing layer a pool from which it
# selects the SAME prior-year baseline — the property the live/replay parity
# of the whole de-boilerplate feature rests on.
#
# Shape is deliberately "AAPL-recently-filed": a current Q2 10-Q filed only
# two weeks before the tick, with several intervening quarters between it and
# its true prior-year counterpart.  A single fixed-offset anchor (the pre-fix
# design) would land on the wrong quarter and silently skip de-boilerplate;
# the pool hands every candidate to ``_find_prior_year_baseline``, which
# matches on ``period_of_report``.
# ---------------------------------------------------------------------------

# Evaluation tick — a fortnight after the current 10-Q was filed.
POOL_AS_OF = datetime(2026, 5, 15, 16, 0, tzinfo=UTC)

# Pool lower bound — the assembly reaches ~800 days back.  Placed before every
# universe filing so the raw pools are identical on both paths (the live
# backfill path reaches even further below ``from_date`` via its own baseline
# range, but those deeper filings are >395 days from the current period and so
# never affect the pairing pick — see the pick-parity assertion below).
POOL_FROM = date(2024, 1, 1)

# AAPL-shaped fiscal quarters: each Q ends ~late in the quarter month.  The
# prior-year baseline for the current Q2 (period 20260328) is the Q2 a year
# earlier (period 20250329) — 364 days back, inside the 335–395 pairing window.
_POOL_UNIVERSE: list[_FakeRawFiling] = [
    _FakeRawFiling("10-Q", date(2026, 5, 1),  "Q-current",   period_of_report="20260328"),  # Q2 FY26
    _FakeRawFiling("10-Q", date(2026, 2, 1),  "Q-mid1",      period_of_report="20251228"),  # Q1 FY26 (~90d)
    _FakeRawFiling("10-Q", date(2025, 8, 1),  "Q-mid2",      period_of_report="20250628"),  # Q3 FY25 (~273d)
    _FakeRawFiling("10-Q", date(2025, 5, 2),  "Q-prioryear", period_of_report="20250329"),  # Q2 FY25 (~364d) ← baseline
    _FakeRawFiling("10-K", date(2024, 11, 1), "K-old",       period_of_report="20240928"),  # FY24 annual
    _FakeRawFiling("8-K",  date(2026, 4, 10), "E-evt"),                                       # event, no period
]


def _pool_latest(symbol: str, form: str, as_of: datetime) -> list[_FakeRawFiling]:
    """Return the single newest ``form`` filing on or before ``as_of``."""
    candidates = [
        raw for raw in _POOL_UNIVERSE
        if raw.form == form and raw.filing_date <= as_of.date()
    ]
    candidates.sort(key=lambda raw: raw.filing_date, reverse=True)
    return candidates[:1]


def _pool_range(
    symbol: str,
    forms: tuple[str, ...],
    lower: datetime,
    upper: datetime,
) -> list[_FakeRawFiling]:
    """Return every filing of ``forms`` filed within ``[lower, upper]``."""
    return [
        raw for raw in _POOL_UNIVERSE
        if raw.form in forms and lower.date() <= raw.filing_date <= upper.date()
    ]


def _pool_universe_as_models() -> list[Filing]:
    """Convert the pool universe into ``Filing`` models, as a backfill writes them."""
    return [
        Filing(
            ticker="AAPL",
            form_type=raw.form,
            filed_at=datetime.combine(raw.filing_date, datetime.min.time(), tzinfo=UTC),
            accession_no=raw.accession_no,
            period_of_report=raw.period_of_report or None,
        )
        for raw in _POOL_UNIVERSE
    ]


@pytest.mark.asyncio
async def test_live_and_replay_pool_yield_same_prioryear_pick(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pool-mode reads must hand the pairing layer the same prior-year baseline.

    Asserts three things, strongest last:

    1. Both paths surface the same set of periodic accessions in the pool.
    2. ``period_of_report`` survives both paths intact (the pairing key).
    3. ``_find_prior_year_baseline`` selects the SAME accession on both pools —
       the only property the de-boilerplate diff actually consumes.
    """
    # ── Live path: EDGAR provider in backfill/pool mode ────────────────────
    import data.providers.filings.edgar as edgar_mod
    from agents.analysts.fundamental.fetch import _find_prior_year_baseline

    monkeypatch.setattr(edgar_mod, "_iter_latest_filing", _pool_latest)
    monkeypatch.setattr(edgar_mod, "_iter_filings_range", _pool_range)

    live = await edgar_mod.fetch(
        "AAPL",
        as_of=POOL_AS_OF,
        staleness_days=STALENESS_DAYS,
        include_excerpts=False,
        from_date=POOL_FROM,
    )

    # ── Replay path: cache provider in pool mode over a seeded store ────────
    store = CachedDataStore(tmp_path / "store.sqlite")
    _store_handle.set_store(store)
    try:
        from backtest.providers import filings_cache

        store.write_filings("AAPL", _pool_universe_as_models())

        replay = await filings_cache.fetch(
            "AAPL",
            as_of=POOL_AS_OF,
            staleness_days=STALENESS_DAYS,
            from_date=POOL_FROM,
        )
    finally:
        _store_handle.clear_store()

    # Restrict to periodic forms — the de-boilerplate pool the assembly keeps.
    periodic = ("10-K", "10-Q")
    live_periodic   = {f.accession_no for f in live   if f.form_type in periodic}
    replay_periodic = {f.accession_no for f in replay if f.form_type in periodic}

    # 1. Identical pools (all universe filings sit at or after POOL_FROM).
    assert live_periodic == replay_periodic == {
        "Q-current", "Q-mid1", "Q-mid2", "Q-prioryear", "K-old",
    }

    # 2. period_of_report survived both paths.
    live_periods = {f.accession_no: f.period_of_report for f in live}
    replay_periods = {f.accession_no: f.period_of_report for f in replay}
    assert live_periods["Q-prioryear"] == replay_periods["Q-prioryear"] == "20250329"

    # 3. The pairing pick is identical — Q2-prior-year, NOT an adjacent quarter.
    live_dicts   = [f.model_dump() for f in live]
    replay_dicts = [f.model_dump() for f in replay]

    live_pick   = _find_prior_year_baseline("20260328", "10-Q", live_dicts)
    replay_pick = _find_prior_year_baseline("20260328", "10-Q", replay_dicts)

    assert live_pick is not None and replay_pick is not None
    assert live_pick["accession_no"] == replay_pick["accession_no"] == "Q-prioryear"
