"""Shared fixtures for data-layer unit tests."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from data import registry as _registry


@pytest.fixture
def registry_isolation() -> Iterator[None]:
    """Snapshot _REGISTRY and _LIMITERS, restore after the test.

    Lets tests register fake providers without leaking into the next
    test or into real provider tests.
    """
    saved_registry = dict(_registry._REGISTRY)
    saved_limiters = dict(_registry._LIMITERS)
    _registry._REGISTRY.clear()
    _registry._LIMITERS.clear()
    try:
        yield
    finally:
        _registry._REGISTRY.clear()
        _registry._LIMITERS.clear()
        _registry._REGISTRY.update(saved_registry)
        _registry._LIMITERS.update(saved_limiters)
