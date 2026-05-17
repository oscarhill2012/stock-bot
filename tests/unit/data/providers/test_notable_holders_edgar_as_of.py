"""``notable_holders/edgar.fetch`` — as_of window, body parsing, and PIT behaviour.

Covers:
- ``fetch`` deriving the filing-date window from ``as_of``, not wall-clock time.
- ``fetch`` swallowing unrecognised kwargs from the registry dispatcher.
- Body parsing: ``percent_of_class``, ``shares_held``, ``purpose_excerpt`` for SC 13D.
- SC 13G filings leave ``purpose_excerpt`` as ``None``.
- ``_parse_cover_page`` and ``_parse_purpose_excerpt`` unit tests.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Fake filing dataclass — shared across body-parse tests
# ---------------------------------------------------------------------------

@dataclass
class _FakeFiling:
    """Minimal edgartools filing stand-in for unit tests.

    Attributes match the attribute names that ``_build`` reads via ``getattr``.
    ``body`` is returned verbatim by the ``.text()`` method so body-parse
    tests can supply synthetic filing text without any EDGAR network calls.
    """

    form: str        = "SC 13D"
    filing_date: Any = date(2023, 3, 10)
    accession_no: str = "x"
    company: str     = "Test Holder LLC"
    body: str        = ""

    def text(self) -> str:
        """Return the fake body text (mirrors edgartools ``Filing.text()``)."""
        return self.body


# ---------------------------------------------------------------------------
# fetch() — as_of window and kwargs passthrough
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_uses_as_of_for_filing_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """``fetch`` derives the filing-date window from ``as_of``."""
    import data.providers.notable_holders.edgar as mod

    captured: dict = {}

    def fake_iter(symbol: str, lookback_days: int, limit: int, as_of: datetime) -> list:
        captured["symbol"]        = symbol
        captured["lookback_days"] = lookback_days
        captured["as_of"]         = as_of
        return []

    monkeypatch.setattr(mod, "_iter_filings", fake_iter)

    await mod.fetch(
        "AAPL",
        lookback_days=180,
        as_of=datetime(2023, 3, 15, 16, 0, tzinfo=UTC),
    )

    assert captured["as_of"] == datetime(2023, 3, 15, 16, 0, tzinfo=UTC)
    assert captured["lookback_days"] == 180


@pytest.mark.asyncio
async def test_fetch_accepts_unrecognised_kwargs(monkeypatch: pytest.MonkeyPatch) -> None:
    """``**_unused`` lets the registry dispatch any kwarg safely."""
    import data.providers.notable_holders.edgar as mod

    monkeypatch.setattr(mod, "_iter_filings", lambda s, lookback, lim, a: [])

    out = await mod.fetch(
        "AAPL",
        as_of=datetime(2023, 3, 15, tzinfo=UTC),
        from_date="ignored",  # type: ignore[call-arg]
    )
    assert out == []


# ---------------------------------------------------------------------------
# fetch() — body parsing (cover page + Item 4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_notable_holders_edgar_parses_cover_page_and_purpose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC 13D: ``percent_of_class``, ``shares_held``, and ``purpose_excerpt`` are parsed.

    The EDGAR limiter is patched to a no-op so no real HTTP rate-limit token
    is consumed during the test.
    """
    import data.providers.notable_holders.edgar as mod

    fake = _FakeFiling(
        form="SC 13D",
        filing_date=date(2023, 3, 10),
        accession_no="x",
        body=(
            "... Percent of Class: 8.5% "
            "Shares Held: 1,200,000 "
            "Item 4. Purpose of Transaction. "
            "The Reporting Person acquired for investment purposes "
            "Item 5. Interest in Securities of the Issuer."
        ),
    )

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [fake])

    # Patch the limiter's acquire() to a no-op coroutine so the test does not
    # block on a real rate-limit bucket.
    async def _noop_acquire() -> None:
        return None

    monkeypatch.setattr(mod._LIMITERS["edgar"], "acquire", _noop_acquire)

    out = await mod.fetch("AAPL", as_of=datetime(2023, 3, 15, tzinfo=UTC), per_form=3)  # type: ignore[call-arg]

    assert len(out) == 1, "Expected exactly one NotableHolder from the fake filing."
    holder = out[0]

    assert abs(holder.percent_of_class - 8.5) < 1e-6, (
        f"percent_of_class should be 8.5, got {holder.percent_of_class}"
    )
    assert holder.shares_held == 1_200_000.0, (
        f"shares_held should be 1200000.0, got {holder.shares_held}"
    )
    assert holder.purpose_excerpt is not None, "SC 13D must have a purpose_excerpt"
    assert "investment purposes" in holder.purpose_excerpt, (
        f"purpose_excerpt should mention 'investment purposes', got: {holder.purpose_excerpt!r}"
    )


@pytest.mark.asyncio
async def test_notable_holders_edgar_13g_has_no_purpose_excerpt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC 13G: ``purpose_excerpt`` must be ``None`` (passive filer, no Item 4 requirement).

    Cover-page fields (``percent_of_class``, ``shares_held``) are still parsed.
    """
    import data.providers.notable_holders.edgar as mod

    fake = _FakeFiling(
        form="SC 13G",
        filing_date=date(2023, 3, 10),
        accession_no="y",
        body=(
            "Percent of Class: 6.2% "
            "Shares Held: 500,000 "
            "Item 4. Purpose of Transaction. "
            "This filer is a passive investor. "
            "Item 5. Interest in Securities of the Issuer."
        ),
    )

    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [fake])

    async def _noop_acquire() -> None:
        return None

    monkeypatch.setattr(mod._LIMITERS["edgar"], "acquire", _noop_acquire)

    out = await mod.fetch("MSFT", as_of=datetime(2023, 3, 15, tzinfo=UTC))

    assert len(out) == 1
    holder = out[0]

    assert abs(holder.percent_of_class - 6.2) < 1e-6
    assert holder.shares_held == 500_000.0
    assert holder.purpose_excerpt is None, (
        "SC 13G must NOT populate purpose_excerpt — passive filer, no Item 4 requirement."
    )


# ---------------------------------------------------------------------------
# _parse_cover_page() — unit tests for the helper directly
# ---------------------------------------------------------------------------

def test_parse_cover_page_full_match() -> None:
    """Both fields parse correctly from a typical cover-page excerpt."""
    from data.providers.notable_holders.edgar import _parse_cover_page

    body = "Percent of Class: 12.34%  Shares Held: 2,500,000"
    pct, shares = _parse_cover_page(body)

    assert abs(pct - 12.34) < 1e-6
    assert shares == 2_500_000.0


def test_parse_cover_page_missing_fields() -> None:
    """Both fields return ``None`` when absent from the body."""
    from data.providers.notable_holders.edgar import _parse_cover_page

    pct, shares = _parse_cover_page("No relevant data here.")

    assert pct is None
    assert shares is None


def test_parse_cover_page_integer_percent() -> None:
    """Whole-number percentage (no decimal point) parses as a float."""
    from data.providers.notable_holders.edgar import _parse_cover_page

    pct, _ = _parse_cover_page("Percent of class - 9 %")

    assert abs(pct - 9.0) < 1e-6


# ---------------------------------------------------------------------------
# _parse_purpose_excerpt() — unit tests for the helper directly
# ---------------------------------------------------------------------------

def test_parse_purpose_excerpt_extracts_item_4() -> None:
    """Item 4 prose is captured up to the Item 5 boundary."""
    from data.providers.notable_holders.edgar import _parse_purpose_excerpt

    body = (
        "Item 4. Purpose of Transaction. "
        "The filer intends to engage with management. "
        "Item 5. Interest in Securities of the Issuer."
    )
    result = _parse_purpose_excerpt(body)

    assert result is not None
    assert "engage with management" in result


def test_parse_purpose_excerpt_returns_none_when_absent() -> None:
    """Returns ``None`` when Item 4 is not present in the body."""
    from data.providers.notable_holders.edgar import _parse_purpose_excerpt

    assert _parse_purpose_excerpt("No items here.") is None


def test_parse_purpose_excerpt_truncates_at_2000_chars() -> None:
    """Excerpt is capped at 2,000 characters regardless of Item 4 length."""
    from data.providers.notable_holders.edgar import _PURPOSE_EXCERPT_MAX, _parse_purpose_excerpt

    long_prose = "A" * 5_000
    body = f"Item 4. Purpose of Transaction. {long_prose} Item 5. Interest."
    result = _parse_purpose_excerpt(body)

    assert result is not None
    assert len(result) <= _PURPOSE_EXCERPT_MAX
