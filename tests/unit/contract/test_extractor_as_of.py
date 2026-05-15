"""``as_of`` drives time-delta features for fundamental; other extractors accept it silently.

DEVIATION from plan: The plan's test used ``as_of=datetime(2023, 1, 31, tzinfo=UTC)``
(naive UTC).  Test uses timezone-aware datetimes throughout since fundamental.py
computes deltas from ``now = as_of`` and the filed_at timestamp is parsed with
fromisoformat which preserves tzinfo; mixing naive/aware would raise a TypeError.
"""
from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import pytest

from contract.extractors.fundamental import extract_fundamental_features


def test_fundamental_days_since_last_filing_uses_as_of() -> None:
    """Same raw bundle, two ``as_of`` values → two different ``days_since_last_filing``."""
    raw = {"filings": [{"filed_at": "2023-01-01T00:00:00+00:00", "form_type": "10-K"}]}

    early = extract_fundamental_features(
        raw, ticker="AAPL", as_of=datetime(2023, 1, 31, tzinfo=UTC),
    )
    late = extract_fundamental_features(
        raw, ticker="AAPL", as_of=datetime(2023, 6, 30, tzinfo=UTC),
    )
    assert late["days_since_last_filing"] > early["days_since_last_filing"]


@pytest.mark.parametrize("module_path", [
    "contract.extractors.technical",
    "contract.extractors.news",
    "contract.extractors.smart_money",
])
def test_clock_free_extractors_accept_as_of(module_path: str) -> None:
    """Every extractor accepts ``as_of`` so the analyst shim can pass it uniformly."""
    module = importlib.import_module(module_path)
    extractor = next(
        v for k, v in vars(module).items()
        if k.startswith("extract_") and callable(v)
    )
    assert "as_of" in inspect.signature(extractor).parameters, (
        f"{extractor.__name__} is missing the as_of parameter"
    )


def test_social_extractor_accepts_as_of() -> None:
    """``extract_social_features`` accepts ``as_of`` uniformly.

    Tested separately from the parametrised batch because importing
    ``contract.extractors.social`` in isolation (via importlib) triggers a
    circular import through agents.analysts.  Direct import works fine because
    the full package is already initialised by the time the test runs.
    """
    from contract.extractors.social import extract_social_features as esf
    assert "as_of" in inspect.signature(esf).parameters, (
        "extract_social_features is missing the as_of parameter"
    )
