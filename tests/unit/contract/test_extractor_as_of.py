"""``as_of`` drives time-delta features for fundamental; other extractors accept it silently."""
from __future__ import annotations

import importlib
import inspect
from datetime import UTC, datetime

import pytest

from contract.extractors.fundamental import extract_fundamental_features


def test_fundamental_days_since_last_filing_uses_as_of() -> None:
    """Same raw bundle, two ``as_of`` values → two different ``days_since_last_filing``."""
    raw = {"filings": [{"filed_at": "2023-01-01T00:00:00+00:00", "form": "10-K"}]}

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
    "contract.extractors.social",
    "contract.extractors.smart_money",
])
def test_clock_free_extractors_accept_as_of(module_path: str) -> None:
    """Every extractor accepts ``as_of`` so the analyst shim can pass it uniformly."""
    module = importlib.import_module(module_path)
    extractor = next(
        v for k, v in vars(module).items()
        if k.startswith("extract_") and callable(v)
    )
    assert "as_of" in inspect.signature(extractor).parameters
