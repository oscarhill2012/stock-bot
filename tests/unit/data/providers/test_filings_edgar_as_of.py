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


# ---------------------------------------------------------------------------
# Amendment-exclusion tests (bug fix: 10-K/A displacing original 10-K)
# ---------------------------------------------------------------------------

def test_is_amendment_true_for_slash_a_forms() -> None:
    """``_is_amendment`` must return True for any form ending in ``/A``.

    These are the partial administrative amendments that should never enter
    the cache or reach the analyst.
    """
    import data.providers.filings.edgar as mod

    assert mod._is_amendment("10-K/A") is True
    assert mod._is_amendment("10-Q/A") is True
    assert mod._is_amendment("8-K/A")  is True


def test_is_amendment_false_for_base_forms() -> None:
    """``_is_amendment`` must return False for all standard base forms.

    The helper must never accidentally exclude a base filing.
    """
    import data.providers.filings.edgar as mod

    assert mod._is_amendment("10-K")  is False
    assert mod._is_amendment("10-Q")  is False
    assert mod._is_amendment("8-K")   is False
    assert mod._is_amendment("")      is False


def test_filter_amendments_removes_slash_a_rows() -> None:
    """``_filter_amendments`` must drop /A objects and preserve base forms.

    This is the pure-iterable choke-point filter that both live and backfill
    paths run through — testable without any network or provider machinery.
    """
    import data.providers.filings.edgar as mod

    base_k   = _FakeFiling("10-K",   date(2025, 1, 31), "K-base")
    amend_k  = _FakeFiling("10-K/A", date(2025, 4, 30), "K-amended")
    base_q   = _FakeFiling("10-Q",   date(2025, 8, 1),  "Q-base")
    amend_q  = _FakeFiling("10-Q/A", date(2025, 9, 5),  "Q-amended")
    base_e   = _FakeFiling("8-K",    date(2026, 2, 20), "E-base")
    amend_e  = _FakeFiling("8-K/A",  date(2026, 2, 22), "E-amended")

    raw = [base_k, amend_k, base_q, amend_q, base_e, amend_e]
    filtered = mod._filter_amendments(raw)

    accessions = {f.accession_no for f in filtered}
    assert accessions == {"K-base", "Q-base", "E-base"}

    # Amendments are excluded — none must survive.
    assert all(not mod._is_amendment(f.form) for f in filtered)


@pytest.mark.asyncio
async def test_amendments_excluded_from_live_latest_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam filter must drop 10-K/A rows returned by the latest-filing query.

    Simulates the TSLA bug: ``get_filings(form="10-K")`` returned a 10-K/A
    amendment, which ``head(1)`` anchored — displacing the original 10-K.
    After the fix the amendment must be silently dropped so the base form
    survives.
    """
    import data.providers.filings.edgar as mod

    # Seam returns the amendment FIRST (as edgartools would for TSLA).
    amendment = _FakeFiling("10-K/A", date(2025, 4, 30), "K-amended")
    original  = _FakeFiling("10-K",   date(2025, 1, 31), "K-original")

    _patch_seams(monkeypatch, mod, latest={"10-K": [amendment, original]}, ranged=[])

    out = await mod.fetch("TSLA", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    accessions = {f.accession_no for f in out}
    assert "K-amended"  not in accessions, "amendment must not reach the analyst"
    assert "K-original" in accessions,     "original must still be present"


@pytest.mark.asyncio
async def test_amendments_excluded_from_backfill_range_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The range query filter must drop 8-K/A rows from backfill mode.

    The production cache contained four stray 8-K/A rows from range queries;
    this test confirms they are stripped before returning the superset.
    """
    import data.providers.filings.edgar as mod

    base_e    = _FakeFiling("8-K",   date(2026, 1, 10), "E-base")
    amend_e   = _FakeFiling("8-K/A", date(2026, 1, 12), "E-amended")
    base_k    = _FakeFiling("10-K",  date(2025, 1, 31), "K-base")
    amend_k   = _FakeFiling("10-K/A",date(2025, 4, 30), "K-amended")

    _patch_seams(
        monkeypatch, mod,
        latest={"10-K": [base_k, amend_k], "10-Q": []},
        ranged=[base_e, amend_e, base_k, amend_k],
    )

    out = await mod.fetch(
        "TSLA",
        as_of=AS_OF,
        staleness_days=90,
        include_excerpts=False,
        from_date=date(2025, 9, 2),
    )

    form_types = {f.form_type for f in out}
    assert "10-K/A" not in form_types, "10-K/A must not enter the backfill cache"
    assert "8-K/A"  not in form_types, "8-K/A must not enter the backfill cache"


@pytest.mark.asyncio
async def test_get_filings_called_with_amendments_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """edgartools ``get_filings`` must be called with ``amendments=False``.

    Verifies the query-level exclusion (layer 1 of the belt-and-braces
    defence).  Monkeypatches the ``Company`` class so the kwarg is recorded
    without any real network activity.
    """
    import data.providers.filings.edgar as mod

    # Accumulate every call's kwargs so we can assert on them.
    kwargs_log: list[dict] = []

    class _FakeFilings:
        """Minimal stand-in for an edgartools EntityFilings object."""

        def head(self, n: int):  # noqa: ANN001
            """Return self — we never actually iterate in this test."""
            return self

        def __iter__(self):
            return iter([])

    class _FakeCompany:
        """Stand-in for edgar.Company that records get_filings kwargs."""

        def __init__(self, symbol: str) -> None:
            self._symbol = symbol

        def get_filings(self, **kwargs) -> _FakeFilings:  # noqa: ANN003
            """Record kwargs and return an empty filings stub."""
            kwargs_log.append(kwargs)
            return _FakeFilings()

    monkeypatch.setattr(mod, "Company", _FakeCompany)

    # Trigger both live-mode paths (latest + range) — we do NOT patch the
    # seams here because we want the real sync helpers to call get_filings.
    import data.secrets as secrets_mod
    monkeypatch.setattr(secrets_mod, "require_key", lambda _key: "test@test.com")

    import edgar as edgar_lib
    monkeypatch.setattr(edgar_lib, "set_identity", lambda _id: None)

    await mod.fetch("AAPL", as_of=AS_OF, staleness_days=90, include_excerpts=False)

    assert kwargs_log, "get_filings was never called"
    for kwargs in kwargs_log:
        assert kwargs.get("amendments") is False, (
            f"get_filings called without amendments=False: {kwargs}"
        )
