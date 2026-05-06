"""Shared fixtures for the StockBot test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_path():
    """Return absolute path to a named JSON fixture under tests/fixtures/."""
    def _get(name: str) -> Path:
        p = FIXTURES / name
        if not p.exists():
            pytest.fail(f"missing fixture: {p}")
        return p
    return _get


@pytest.fixture
def load_fixture(fixture_path):
    """Load a JSON fixture as a Python object."""
    def _load(name: str):
        with fixture_path(name).open() as f:
            return json.load(f)
    return _load
