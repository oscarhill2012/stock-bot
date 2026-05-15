"""Tests for the in-process provider-swap helper."""
from __future__ import annotations

import pytest

from data import registry
from data.config import get_config


def test_set_active_provider_round_trips() -> None:
    """``set_active_provider`` updates the config and ``restore`` reverts it."""
    original = get_config().providers["news"]

    restore = registry.set_active_provider("news", "cache")
    assert get_config().providers["news"] == "cache"

    restore()
    assert get_config().providers["news"] == original


def test_set_active_provider_rejects_unknown_domain() -> None:
    """Unknown domain name raises ValueError."""
    with pytest.raises(ValueError, match="unknown domain"):
        registry.set_active_provider("not_a_domain", "cache")
