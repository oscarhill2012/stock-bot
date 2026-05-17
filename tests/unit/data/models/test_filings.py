"""Unit tests for Phase 1 extensions to ``Filing``."""
from __future__ import annotations

from datetime import UTC, datetime

from data.models.filings import Filing


def test_filing_accepts_body_excerpt_and_items() -> None:
    """8-K body fields are accepted when populated."""
    f = Filing(
        ticker="AAPL", form_type="8-K",
        filed_at=datetime(2023, 3, 10, 12, 0, tzinfo=UTC),
        accession_no="0000000000-00-000001",
        url="https://sec.gov/dummy",
        body_excerpt="Apple Inc. announced...",
        items_8k=["2.02", "9.01"],
    )
    assert f.body_excerpt.startswith("Apple")
    assert f.items_8k == ["2.02", "9.01"]


def test_filing_new_fields_default() -> None:
    """New 8-K fields default to None / empty list — back-compat."""
    f = Filing(
        ticker="AAPL", form_type="10-K",
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        accession_no="x", url="https://sec.gov/dummy",
    )
    assert f.body_excerpt is None
    assert f.items_8k == []


def test_filing_new_fields_round_trip() -> None:
    """New fields survive model_dump → model_validate round-trip."""
    f = Filing(
        ticker="TSLA", form_type="8-K",
        filed_at=datetime(2023, 3, 10, tzinfo=UTC),
        accession_no="y", url="https://sec.gov/dummy",
        body_excerpt="Tesla announced Q4 results.",
        items_8k=["2.02"],
    )
    restored = Filing.model_validate(f.model_dump())
    assert restored == f
