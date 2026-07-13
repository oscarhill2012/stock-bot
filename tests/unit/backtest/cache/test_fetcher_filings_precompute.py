"""Fetcher wiring test — filings precompute reaches persisted storage.

Pins the full pool -> ``compute_filing_similarities`` -> ``write_filings`` chain
inside ``Fetcher._fetch_one``'s ``domain == "filings"`` branch.  The isolated
``compute_filing_similarities`` function has its own unit test; THIS test guards
the integration wiring, so a future refactor that silently drops the precompute
call in the fetcher fails loudly here rather than shipping null cosines.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from backtest.cache.fetcher import Fetcher
from backtest.cache.store import CachedDataStore
from backtest.windows import Window
from data.models import Filing


def _q(accession: str, period: str, filed: datetime, mda: str) -> Filing:
    """Build a minimal 10-Q ``Filing`` for the fetcher pool.

    Parameters
    ----------
    accession:
        Accession number (primary key in the store).
    period:
        ``period_of_report`` ISO date driving prior-year pairing.
    filed:
        ``filed_at`` timestamp (aware — SQLite persistence needs a real stamp).
    mda:
        MD&A prose scored by ``compute_similarity``.

    Returns
    -------
    Filing
        A 10-Q filing for ticker ``AAPL``.
    """
    return Filing(
        ticker="AAPL", form_type="10-Q", accession_no=accession,
        filed_at=filed, period_of_report=period, mda_excerpt=mda,
    )


@pytest.mark.asyncio
async def test_filings_fetch_persists_precomputed_cosine(tmp_path: Path) -> None:
    """A paired in-window filing must land in the store with a non-null cosine.

    Drives the real ``Fetcher`` against a real ``CachedDataStore`` with a fake
    filings provider returning a two-filing pool: a current 10-Q and its
    same-quarter prior-year 10-Q.  After the fetch, the persisted current filing
    must carry a populated ``mda_cosine_vs_prior`` (proving the precompute ran
    before the write), while the oldest/unpaired prior filing must keep ``None``.
    """
    store  = CachedDataStore(tmp_path / "store.sqlite")
    window = Window(
        start=date(2024, 1, 1), end=date(2024, 12, 31),
        notes="", risk_free_rate_annual=0.048,
    )

    # A near-verbatim (number-only change) prior/current pair — pairing must
    # find the prior, and the cosine must be high because only a figure moved.
    # Both sides are deliberately full-length (> 400 chars, the stub
    # threshold) so this exercises pairing + scoring, not the stub guard.
    prior = _q(
        "p", "2023-06-30", datetime(2023, 8, 1, tzinfo=UTC),
        "Revenue was 12.1 billion this quarter with strong demand across "
        "every reportable segment, driven by continued expansion in our "
        "core markets and disciplined cost management throughout the "
        "period, partially offset by adverse currency translation effects "
        "in our international operations and modestly higher input costs "
        "across the supply chain, as well as incremental investment in "
        "research and development intended to sustain our competitive "
        "position over the medium term.",
    )
    current = _q(
        "c", "2024-06-30", datetime(2024, 8, 1, tzinfo=UTC),
        "Revenue was 13.4 billion this quarter with strong demand across "
        "every reportable segment, driven by continued expansion in our "
        "core markets and disciplined cost management throughout the "
        "period, partially offset by adverse currency translation effects "
        "in our international operations and modestly higher input costs "
        "across the supply chain, as well as incremental investment in "
        "research and development intended to sustain our competitive "
        "position over the medium term.",
    )

    # Fake provider fn — the Fetcher calls it as fn(ticker, start=, end=); it
    # ignores the window bounds and hands back the fixed pool.
    async def _fake_filings(ticker: str, *, start, end) -> list[Filing]:
        """Return the fixed two-filing pool regardless of window bounds."""
        return [current, prior]

    fetcher = Fetcher(
        store=store,
        window_key="precompute-test",
        window=window,
        watchlist=["AAPL"],
        provider_fns={"filings": _fake_filings},
        live_providers_for_domain={"filings": "edgar"},
    )

    await fetcher.run()

    # Read the persisted rows back and key them by accession for assertion.
    persisted = {
        f.accession_no: f
        for f in store.read_filings("AAPL", as_of=datetime(2024, 12, 31, tzinfo=UTC))
    }

    # The paired current filing must have a persisted, populated cosine — this
    # only holds if compute_filing_similarities ran before write_filings.
    assert persisted["c"].mda_cosine_vs_prior is not None
    assert persisted["c"].mda_cosine_vs_prior > 0.9   # number-only change

    # The oldest filing in the pool has no prior pair — correct None absence.
    assert persisted["p"].mda_cosine_vs_prior is None
