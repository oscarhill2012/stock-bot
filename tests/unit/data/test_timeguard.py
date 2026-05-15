"""Unit tests for ``data.timeguard.resolve_as_of``.

Verifies:
- candidate is returned verbatim when non-None
- wall-clock fallback fires when allowed and strict mode is off
- AsOfRequiredError raised when strict mode is on, regardless of allow_wallclock
- AsOfRequiredError raised when allow_wallclock is False, regardless of strict
- the `site` argument appears in the error message
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from data.timeguard import AsOfRequiredError, resolve_as_of


def test_returns_candidate_when_supplied() -> None:
    """A non-None candidate must be returned unchanged regardless of flags."""
    fixed = datetime(2023, 3, 10, 9, 30, tzinfo=UTC)
    assert resolve_as_of(fixed, allow_wallclock=False, site="x") is fixed
    assert resolve_as_of(fixed, allow_wallclock=True,  site="x") is fixed


def test_falls_back_to_wallclock_when_allowed_and_not_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When live (no strict env) and allow_wallclock=True, returns datetime.now(UTC)."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    before = datetime.now(tz=UTC)
    got    = resolve_as_of(None, allow_wallclock=True, site="live")
    after  = datetime.now(tz=UTC)

    assert before <= got <= after
    assert got.tzinfo is not None


def test_raises_in_strict_mode_even_if_wallclock_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STOCKBOT_STRICT_AS_OF=1 must veto wall-clock fallback unconditionally."""
    monkeypatch.setenv("STOCKBOT_STRICT_AS_OF", "1")

    with pytest.raises(AsOfRequiredError) as exc:
        resolve_as_of(None, allow_wallclock=True, site="aggregator")

    assert "aggregator" in str(exc.value)


def test_raises_when_wallclock_not_allowed_even_outside_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """allow_wallclock=False is its own veto — strict env not required."""
    monkeypatch.delenv("STOCKBOT_STRICT_AS_OF", raising=False)

    with pytest.raises(AsOfRequiredError):
        resolve_as_of(None, allow_wallclock=False, site="news_fetch")
