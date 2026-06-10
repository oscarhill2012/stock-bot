"""Shared fixtures for the StockBot test suite."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from config.analysts import get_analysts_config

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _clear_analysts_config_cache():
    """Ensure each test starts with a fresh AnalystsConfig.

    The ``@lru_cache(maxsize=1)`` on ``get_analysts_config`` would otherwise
    let one test's loaded config leak into the next test's callbacks (e.g.
    ``_caps()`` in ``news/fetch.py`` and ``fundamental/fetch.py``).
    """
    get_analysts_config.cache_clear()
    yield
    get_analysts_config.cache_clear()


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


from tests._helpers import assert_no_silent_degradation, make_tick_state  # noqa: E402

# Re-export as pytest fixtures for tests that prefer DI.

@pytest.fixture
def degradation_check():
    """Fixture form of ``assert_no_silent_degradation`` — accepts kwargs.

    Usage:
        def test_x(degradation_check):
            ...
            degradation_check(state)
            degradation_check(state, allow_degradation=("news",))
    """
    return assert_no_silent_degradation


@pytest.fixture
def tick_state():
    """Fixture form of ``make_tick_state`` — call it to build state.

    Usage:
        def test_x(tick_state):
            state = tick_state(watchlist=["AAPL"], held={"AAPL": 5.0})
    """
    return make_tick_state
