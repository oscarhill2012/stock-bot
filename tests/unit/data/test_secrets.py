"""Unit tests for data.secrets — env-var reader."""
from __future__ import annotations

import pytest

from data.secrets import SecretMissingError, require_key


def test_require_key_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCKBOT_TEST_KEY", "abc123")
    assert require_key("STOCKBOT_TEST_KEY") == "abc123"


def test_require_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCKBOT_MISSING_KEY", raising=False)
    with pytest.raises(SecretMissingError, match="STOCKBOT_MISSING_KEY"):
        require_key("STOCKBOT_MISSING_KEY")


def test_require_key_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCKBOT_EMPTY_KEY", "")
    with pytest.raises(SecretMissingError):
        require_key("STOCKBOT_EMPTY_KEY")
