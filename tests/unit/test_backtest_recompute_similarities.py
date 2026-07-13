"""Tests for the no-network filing-similarity recompute script.

``scripts.backtest_recompute_similarities`` re-scores already-cached filing
text after a scoring-rule change (the v1.1 stub guard) without re-hitting
EDGAR. These tests drive ``recompute_window_similarities`` directly against
a real ``CachedDataStore`` fixture — the script's testable core — rather than
shelling out to ``main()``, which touches real config files
(``config/backtest_windows.json`` / ``config/watchlist.json``) that are not
test fixtures.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import Filing
from scripts.backtest_recompute_similarities import recompute_window_similarities

# Full-length MD&A prose (> 400 chars — the stub threshold), reused so the
# "full pair" fixture below exercises genuine scoring rather than the guard.
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
_STUB_MDA = "Item 7. MD&A is incorporated by reference. See page 59."


@pytest.fixture
def store(tmp_path: Path) -> CachedDataStore:
    """Fresh empty cache store rooted in a temp dir."""
    return CachedDataStore(tmp_path / "store.sqlite")


def test_recompute_scores_full_pair_and_clears_stub_pair(store: CachedDataStore) -> None:
    """A mixed pool (one full pair, one stub pair) ends correctly scored/cleared.

    AAPL carries a full-text prior/current pair — must end with a real
    cosine. MSFT carries a stub-vs-full pair whose current filing already
    persists a STALE cosine from before the v1.1 guard existed — the
    recompute must clear it to NULL (proving the store round-trip, not just
    the in-memory computation, is authoritative).
    """
    aapl_prior = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="aapl-p",
        filed_at=datetime(2023, 8, 1, tzinfo=UTC), period_of_report="2023-06-30",
        mda_excerpt=_FULL_PRIOR_MDA,
    )
    aapl_current = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="aapl-c",
        filed_at=datetime(2024, 8, 1, tzinfo=UTC), period_of_report="2024-06-30",
        mda_excerpt=_FULL_CURRENT_MDA,
    )
    msft_prior = Filing(
        ticker="MSFT", form_type="10-Q", accession_no="msft-p",
        filed_at=datetime(2023, 8, 1, tzinfo=UTC), period_of_report="2023-06-30",
        mda_excerpt=_STUB_MDA,
    )
    # Pre-existing stale cosine from a run predating the stub guard.
    msft_current = Filing(
        ticker="MSFT", form_type="10-Q", accession_no="msft-c",
        filed_at=datetime(2024, 8, 1, tzinfo=UTC), period_of_report="2024-06-30",
        mda_excerpt=_STUB_MDA,
        mda_cosine_vs_prior=0.99, mda_jaccard_vs_prior=0.98,
    )

    store.write_filings("AAPL", [aapl_prior, aapl_current])
    store.write_filings("MSFT", [msft_prior, msft_current])

    summary = recompute_window_similarities(store, ["AAPL", "MSFT"])

    far_future = datetime(2100, 1, 1, tzinfo=UTC)
    aapl_rows = {f.accession_no: f for f in store.read_filings("AAPL", as_of=far_future)}
    msft_rows = {f.accession_no: f for f in store.read_filings("MSFT", as_of=far_future)}

    # Full pair: current filing ends with a real cosine.
    assert aapl_rows["aapl-c"].mda_cosine_vs_prior is not None
    assert aapl_rows["aapl-c"].mda_cosine_vs_prior > 0.9

    # Stub pair: the stale cosine must be cleared to NULL, not left in place.
    assert msft_rows["msft-c"].mda_cosine_vs_prior is None
    assert msft_rows["msft-c"].mda_jaccard_vs_prior is None

    # Summary tallies reflect the same outcome.
    assert summary["AAPL"]["written"] >= 1
    assert summary["MSFT"]["cleared"] >= 1
    assert summary["_total"]["written"] == sum(
        summary[t]["written"] for t in ("AAPL", "MSFT")
    )


def test_recompute_touches_no_network(store: CachedDataStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """The recompute must never call an EDGAR/network provider.

    Guards the brief's "touch no network" constraint: if a future change
    wires in a provider import, this test fails loudly instead of the
    recompute silently starting to make network calls.
    """
    filing = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="aapl-c",
        filed_at=datetime(2024, 8, 1, tzinfo=UTC), period_of_report="2024-06-30",
        mda_excerpt=_FULL_CURRENT_MDA,
    )
    store.write_filings("AAPL", [filing])

    # Poison any attempt to open a network socket — a network-touching
    # recompute would raise here instead of silently succeeding.
    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("recompute_window_similarities must not touch the network")

    monkeypatch.setattr("socket.socket.connect", _blocked, raising=True)

    # Must complete without raising — no network call is attempted.
    recompute_window_similarities(store, ["AAPL"])
