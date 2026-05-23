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


def test_parses_iso_string_candidate_to_datetime() -> None:
    """An ISO-8601 string candidate (from DatabaseSessionService JSON coercion)
    must be parsed back to a timezone-aware datetime rather than returned as a
    string — which would cause AttributeError on downstream .month/.year usage.
    """
    fixed   = datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
    iso_str = fixed.isoformat()  # e.g. "2024-06-15T12:30:00+00:00"

    result = resolve_as_of(iso_str, allow_wallclock=True, site="db_coercion")

    assert isinstance(result, datetime), (
        f"Expected datetime, got {type(result).__name__}"
    )
    assert result == fixed
    # Verify arithmetic doesn't raise — this is the attribute class the bug kills.
    _ = result.month
    _ = result.year


def test_raises_on_malformed_iso_string_candidate() -> None:
    """A non-ISO string must raise ValueError rather than silently pass through."""
    with pytest.raises(ValueError, match="ISO-8601"):
        resolve_as_of("not-a-date", allow_wallclock=True, site="bad_site")


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
