"""``filings/edgar.fetch`` filters by ``as_of`` so backfill ignores future filings."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest

# ---------------------------------------------------------------------------
# Shared fake edgartools filing stub
# ---------------------------------------------------------------------------

@dataclass
class _FakeFiling:
    """Stand-in for an edgartools Filing object.

    Exposes the same attributes the provider reads — ``form``,
    ``filing_date``, ``accession_no``, ``items``, ``text()``, ``mda``.

    NB: ``items`` is a comma-delimited string matching the real edgartools
    shape (smoke A6).  The provider must split on comma — NOT iterate the
    string character-by-character.
    """

    form: str
    filing_date: date
    accession_no: str
    items: str = ""       # comma-delimited, e.g. "2.02,9.01"
    body: str = ""
    mda: str | None = None

    def text(self) -> str:
        """Return the filing body text."""
        return self.body


# ---------------------------------------------------------------------------
# Original as_of / extra-kwargs tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_passes_as_of_to_lister(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_iter_filings`` must receive ``as_of`` and pass it through as the upper bound.

    The test patches ``_iter_filings`` (the monkeypatchable seam over
    ``_list_filings``) so no real edgartools or network calls are made.
    """
    import data.providers.filings.edgar as mod

    captured: dict = {}

    def fake_iter(symbol: str, form_types: tuple, limit: int, as_of: datetime) -> list:
        captured["symbol"]     = symbol
        captured["form_types"] = form_types
        captured["limit"]      = limit
        captured["as_of"]      = as_of
        return []

    monkeypatch.setattr(mod, "_iter_filings", fake_iter)

    await mod.fetch(
        "AAPL",
        form_types=("10-K", "10-Q"),
        limit=5,
        include_excerpts=False,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"]      == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["form_types"] == ("10-K", "10-Q")
    assert captured["limit"]      == 5


@pytest.mark.asyncio
async def test_fetch_accepts_extra_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` should accept and silently discard unknown keyword arguments via ``**_unused``."""
    import data.providers.filings.edgar as mod

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [])
    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        lookback_days=30,  # type: ignore[call-arg]
    )
    assert out == []


# ---------------------------------------------------------------------------
# Phase 7 — audit 2.7: body_excerpt and items_8k population
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
        filing_date=date(2023, 3, 10),
        accession_no="0000000000-00-000001",
        items="2.02,9.01",
        body="Apple Inc. reported..." * 200,
    )

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [fake_filing])

    # ``include_excerpts=False`` avoids the EDGAR identity/auth round-trip;
    # body_excerpt and items_8k are populated for 8-K regardless of this flag.
    out = await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 15),
        per_form=3,          # type: ignore[call-arg]
        include_excerpts=False,
    )

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
        filing_date=date(2023, 3, 10),
        accession_no="x",
        items="7.01, 8.01",
        body="b",
    )
    empty = _FakeFiling(
        form="8-K",
        filing_date=date(2023, 3, 11),
        accession_no="y",
        items="",
        body="b",
    )

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [spaced, empty])

    out = await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 15),
        per_form=3,          # type: ignore[call-arg]
        include_excerpts=False,
    )

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
        filing_date=date(2023, 2, 15),
        accession_no="z",
        items="",
        body="Annual report body...",
    )

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [ten_k])

    out = await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 15),
        include_excerpts=False,
    )

    ten_k_filing = [f for f in out if f.form_type == "10-K"][0]
    assert ten_k_filing.items_8k == []
    assert ten_k_filing.body_excerpt is None


@pytest.mark.asyncio
async def test_fetch_accepts_plain_date_as_of(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` should accept a plain ``date`` for ``as_of``, not just ``datetime``."""
    import data.providers.filings.edgar as mod

    captured: dict = {}

    def fake_iter(symbol: str, form_types: tuple, limit: int, as_of: datetime) -> list:
        captured["as_of"] = as_of
        return []

    monkeypatch.setattr(mod, "_iter_filings", fake_iter)

    await mod.fetch(
        "AAPL",
        as_of=date(2023, 3, 15),
        include_excerpts=False,
    )

    # Plain date should be coerced to a UTC-aware datetime.
    assert isinstance(captured["as_of"], datetime)
    assert captured["as_of"].tzinfo is not None
