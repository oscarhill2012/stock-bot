"""``filings/edgar.fetch`` — per-form queries, shared selector, backfill mode.

2026-06-11 redesign: the provider no longer fetches "latest N filings".
Live mode issues three queries (latest 10-K, latest 10-Q, 8-Ks within the
staleness horizon) and applies the shared ``select_current_filings`` rule.
Backfill mode (``from_date`` given) returns the raw superset a backtest
cache needs: every filing in ``[from_date, as_of]`` plus the anchor set as
of ``from_date`` — with **no** selection, because selection happens at
replay-read time via the same shared rule.

All tests monkeypatch the two seams (``_iter_latest_filing`` and
``_iter_filings_range``) so no real edgartools or network calls are made.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pytest

# ---------------------------------------------------------------------------
# Shared fake edgartools filing stub
# ---------------------------------------------------------------------------

@dataclass
class _FakeFiling:
    """Stand-in for an edgartools Filing object.

    Exposes the same attributes the provider reads — ``form``,
    ``filing_date``, ``accession_no``, ``items``, ``text()``.

    NB: ``items`` is a comma-delimited string matching the real edgartools
    shape (smoke A6).  The provider must split on comma — NOT iterate the
    string character-by-character.
    """

    form: str
    filing_date: date
    accession_no: str
    items: str = ""       # comma-delimited, e.g. "2.02,9.01"
    body: str = ""

    def text(self) -> str:
        """Return the filing body text."""
        return self.body


def _patch_seams(
    monkeypatch: pytest.MonkeyPatch,
    mod,
    latest: dict[str, list],
    ranged: list,
    captured: dict | None = None,
) -> None:
    """Patch both EDGAR listing seams with recording stubs.

    Parameters
    ----------
    monkeypatch:
        pytest monkeypatch fixture.
    mod:
        The ``data.providers.filings.edgar`` module.
    latest:
        Mapping of form type → raw filings the latest-instance seam returns.
    ranged:
        Raw filings every range query returns (tests needing per-call
        results should patch the seam themselves).
    captured:
        Optional dict that accumulates the seam calls for assertions.
    """
    def fake_latest(symbol: str, form: str, as_of: datetime) -> list:
        if captured is not None:
            captured.setdefault("latest_calls", []).append((symbol, form, as_of))
        return latest.get(form, [])

    def fake_range(symbol: str, forms: tuple, lower: datetime, upper: datetime) -> list:
        if captured is not None:
            captured.setdefault("range_calls", []).append((symbol, forms, lower, upper))
        return ranged

    monkeypatch.setattr(mod, "_iter_latest_filing", fake_latest)
    monkeypatch.setattr(mod, "_iter_filings_range", fake_range)


AS_OF = datetime(2026, 3, 2, 16, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Live mode — query shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_mode_issues_per_form_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live fetch asks for the latest 10-K, the latest 10-Q, and the 8-K range.

    The 8-K range must span exactly ``[as_of - staleness_days, as_of]`` —
    that horizon is the analyst-visibility rule's one tuning knob.
    """
    import data.providers.filings.edgar as mod

    captured: dict = {}
    _patch_seams(monkeypatch, mod, latest={}, ranged=[], captured=captured)

    await mod.fetch("AAPL", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    latest_forms = {(form) for _, form, _ in captured["latest_calls"]}
    assert latest_forms == {"10-K", "10-Q"}
    assert all(a == AS_OF for _, _, a in captured["latest_calls"])

    assert len(captured["range_calls"]) == 1
    _, forms, lower, upper = captured["range_calls"][0]
    assert forms == ("8-K",)
    assert upper == AS_OF
    assert lower == AS_OF - timedelta(days=90)


@pytest.mark.asyncio
async def test_live_mode_applies_shared_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live output is the shared rule's selection, not the raw query results.

    A sloppy upstream returning a superseded 10-K and a future-dated 8-K
    must be filtered: only the latest 10-K and PIT-visible fresh 8-Ks
    survive.  This pins ``fetch`` to ``select_current_filings`` so live and
    replay cannot drift.
    """
    import data.providers.filings.edgar as mod

    stale_10k = _FakeFiling("10-K", date(2025, 1, 31), "K-old")
    fresh_10k = _FakeFiling("10-K", date(2026, 1, 30), "K-new")
    future_8k = _FakeFiling("8-K",  date(2026, 3, 5),  "E-future")   # after AS_OF
    fresh_8k  = _FakeFiling("8-K",  date(2026, 2, 20), "E-fresh")

    _patch_seams(
        monkeypatch, mod,
        latest={"10-K": [stale_10k, fresh_10k]},
        ranged=[future_8k, fresh_8k],
    )

    out = await mod.fetch("AAPL", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    accessions = {f.accession_no for f in out}
    assert accessions == {"K-new", "E-fresh"}


# ---------------------------------------------------------------------------
# Backfill mode — raw superset, no selection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_mode_returns_range_plus_anchors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``from_date`` switches to backfill: range + anchor queries, raw union.

    The cache must hold every filing any tick could select, so backfill
    keeps superseded periodic filings (two 10-Qs both survive) — selection
    happens per-tick at replay-read time, not at fill time.
    """
    import data.providers.filings.edgar as mod

    window_start = date(2025, 9, 2)

    anchor_10k = _FakeFiling("10-K", date(2024, 11, 1), "K-anchor")
    anchor_10q = _FakeFiling("10-Q", date(2025, 8, 1),  "Q-anchor")
    in_range_q = _FakeFiling("10-Q", date(2025, 11, 3), "Q-mid")
    in_range_e = _FakeFiling("8-K",  date(2025, 10, 7), "E-mid")

    captured: dict = {}

    def fake_latest(symbol: str, form: str, as_of: datetime) -> list:
        captured.setdefault("latest_calls", []).append((symbol, form, as_of))
        return {"10-K": [anchor_10k], "10-Q": [anchor_10q]}.get(form, [])

    def fake_range(symbol: str, forms: tuple, lower: datetime, upper: datetime) -> list:
        captured.setdefault("range_calls", []).append((symbol, forms, lower, upper))
        # Main window range vs pre-window 8-K anchor range.
        if upper == AS_OF:
            return [in_range_q, in_range_e]
        return []

    monkeypatch.setattr(mod, "_iter_latest_filing", fake_latest)
    monkeypatch.setattr(mod, "_iter_filings_range", fake_range)

    out = await mod.fetch(
        "AAPL",
        as_of=AS_OF,
        staleness_days=90,
        include_excerpts=False,
        from_date=window_start,
    )

    # Raw union — anchors AND in-window rows, superseded forms kept.
    assert {f.accession_no for f in out} == {"K-anchor", "Q-anchor", "Q-mid", "E-mid"}

    # Anchor queries must be pinned to window start, not window end.
    anchor_as_ofs = {a for _, _, a in captured["latest_calls"]}
    assert anchor_as_ofs == {datetime.combine(window_start, datetime.min.time(), tzinfo=UTC)}

    # Two range queries: the window body and the pre-window 8-K staleness reach.
    spans = [(forms, lower, upper) for _, forms, lower, upper in captured["range_calls"]]
    window_lower = datetime.combine(window_start, datetime.min.time(), tzinfo=UTC)
    assert (("10-K", "10-Q", "8-K"), window_lower, AS_OF) in spans
    assert ((("8-K",)), window_lower - timedelta(days=90), window_lower) in spans


@pytest.mark.asyncio
async def test_backfill_mode_dedupes_by_accession(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A filing caught by both the range and an anchor query appears once."""
    import data.providers.filings.edgar as mod

    dup = _FakeFiling("10-Q", date(2025, 9, 10), "Q-dup")

    _patch_seams(monkeypatch, mod, latest={"10-Q": [dup]}, ranged=[dup])

    out = await mod.fetch(
        "AAPL",
        as_of=AS_OF,
        staleness_days=90,
        include_excerpts=False,
        from_date=date(2025, 9, 2),
    )

    assert [f.accession_no for f in out] == ["Q-dup"]


# ---------------------------------------------------------------------------
# Phase 7 — audit 2.7: body_excerpt and items_8k population (unchanged contract)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_filings_edgar_populates_8k_body_and_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """8-K filings must have ``body_excerpt`` (≤ 1500 chars) and ``items_8k``
    populated from the comma-delimited ``filing.items`` string.

    Smoke A6 confirms that edgartools returns ``filing.items`` as a plain
    string; naive ``list(filing.items)`` would iterate char-by-char.  This
    test verifies the provider correctly splits on comma and strips whitespace.
    """
    import data.providers.filings.edgar as mod

    fake_filing = _FakeFiling(
        form="8-K",
        filing_date=date(2026, 2, 20),
        accession_no="0000000000-00-000001",
        items="2.02,9.01",
        body="Apple Inc. reported..." * 200,
    )

    _patch_seams(monkeypatch, mod, latest={}, ranged=[fake_filing])

    # ``include_excerpts=False`` avoids the EDGAR identity/auth round-trip;
    # body_excerpt and items_8k are populated for 8-K regardless of this flag.
    out = await mod.fetch("AAPL", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    eight_k = [f for f in out if f.form_type == "8-K"][0]

    # Items must be a list of clean codes — NOT individual characters.
    assert eight_k.items_8k == ["2.02", "9.01"]
    assert eight_k.body_excerpt is not None
    assert len(eight_k.body_excerpt) <= 1500


@pytest.mark.asyncio
async def test_filings_edgar_handles_whitespace_and_empty_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Edgartools sometimes inserts a space after the comma; some filings
    also have no items at all (8-K with only an exhibit).  Both cases must
    be handled cleanly — no empty-string entries in ``items_8k``.
    """
    import data.providers.filings.edgar as mod

    spaced = _FakeFiling(
        form="8-K",
        filing_date=date(2026, 2, 20),
        accession_no="x",
        items="7.01, 8.01",
        body="b",
    )
    empty = _FakeFiling(
        form="8-K",
        filing_date=date(2026, 2, 21),
        accession_no="y",
        items="",
        body="b",
    )

    _patch_seams(monkeypatch, mod, latest={}, ranged=[spaced, empty])

    out = await mod.fetch("AAPL", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    by_acc = {f.accession_no: f for f in out if f.form_type == "8-K"}
    assert by_acc["x"].items_8k == ["7.01", "8.01"]
    assert by_acc["y"].items_8k == []


@pytest.mark.asyncio
async def test_filings_edgar_non_8k_has_empty_items_8k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-8-K forms (10-K, 10-Q) must always have an empty ``items_8k``
    list and ``None`` for ``body_excerpt`` — they don't carry Item headers.
    """
    import data.providers.filings.edgar as mod

    ten_k = _FakeFiling(
        form="10-K",
        filing_date=date(2026, 1, 30),
        accession_no="z",
        items="",
        body="Annual report body...",
    )

    _patch_seams(monkeypatch, mod, latest={"10-K": [ten_k]}, ranged=[])

    out = await mod.fetch("AAPL", as_of=AS_OF, include_excerpts=False)

    ten_k_filing = [f for f in out if f.form_type == "10-K"][0]
    assert ten_k_filing.items_8k == []
    assert ten_k_filing.body_excerpt is None


# ---------------------------------------------------------------------------
# Signature hygiene
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_accepts_plain_date_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` should accept a plain ``date`` for ``as_of``, not just ``datetime``."""
    import data.providers.filings.edgar as mod

    captured: dict = {}
    _patch_seams(monkeypatch, mod, latest={}, ranged=[], captured=captured)

    await mod.fetch("AAPL", as_of=date(2026, 3, 2), include_excerpts=False)

    # Plain date should be coerced to a UTC-aware datetime everywhere.
    assert all(isinstance(a, datetime) and a.tzinfo is not None
               for _, _, a in captured["latest_calls"])


@pytest.mark.asyncio
async def test_fetch_accepts_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` swallows legacy and cross-domain kwargs via ``**_unused``.

    ``limit``/``form_types``/``lookback_days`` were the pre-redesign knobs;
    the registry also dispatches kwargs meant for other domains.  None may
    raise.
    """
    import data.providers.filings.edgar as mod

    _patch_seams(monkeypatch, mod, latest={}, ranged=[])

    out = await mod.fetch(
        "AAPL",
        as_of=AS_OF,
        include_excerpts=False,
        limit=5,             # type: ignore[call-arg]
        form_types=("10-K",),  # type: ignore[call-arg]
        lookback_days=30,    # type: ignore[call-arg]
    )
    assert out == []
