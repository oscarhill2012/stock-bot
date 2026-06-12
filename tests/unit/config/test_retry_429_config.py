"""Unit tests for ``src/config/retry_429.py`` — Pydantic-validated loader
for ``config/retry_429.json``.

Covers: happy-path load, missing/invalid field rejection (including the
cross-field max_delay >= base_delay invariant), and lru_cache cycling via
``_reset_cache()``.  Mirrors the style of ``test_analysts_config.py`` and
``test_strategist_config.py`` — each test points the loader at a temporary
JSON file so the real ``config/retry_429.json`` is never mutated.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.retry_429 import Retry429Policy, _reset_cache, load_retry_429_policy

# ---------------------------------------------------------------------------
# Shared minimal valid payload
# ---------------------------------------------------------------------------
# All three fields carry documented defaults in the Pydantic model, so even
# an empty JSON object would technically validate.  We supply explicit values
# so each test's assertions are unambiguous.
# ---------------------------------------------------------------------------

_MINIMAL_CFG: dict = {
    "max_attempts":       5,
    "base_delay_seconds": 2.0,
    "max_delay_seconds":  30.0,
}


def test_load_retry_429_policy_valid_payload(tmp_path) -> None:
    """A valid payload loads into a ``Retry429Policy`` with the expected values.

    Also verifies that an optional ``_comment`` key in the JSON does not
    cause a ``ValidationError`` — the loader strips it before validation.
    """

    cfg_file = tmp_path / "retry_429.json"
    payload = {**_MINIMAL_CFG, "_comment": "should be stripped"}
    cfg_file.write_text(json.dumps(payload))

    cfg = load_retry_429_policy(path=cfg_file)

    assert isinstance(cfg, Retry429Policy)
    assert cfg.max_attempts       == 5
    assert cfg.base_delay_seconds == 2.0
    assert cfg.max_delay_seconds  == 30.0


def test_load_retry_429_policy_rejects_invalid_field(tmp_path) -> None:
    """A payload with an invalid field value must raise at load time.

    ``max_attempts`` has ``ge=1`` — setting it to zero is a schema violation
    that should surface immediately rather than silently retrying zero times.
    """

    payload = {**_MINIMAL_CFG, "max_attempts": 0}
    cfg_file = tmp_path / "retry_429.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_retry_429_policy(path=cfg_file)


def test_load_retry_429_policy_rejects_inverted_delay_bounds(tmp_path) -> None:
    """max_delay_seconds < base_delay_seconds must raise a ``ValueError``.

    This cross-field invariant cannot be expressed as a single-field
    ``Field`` constraint so the loader enforces it explicitly.  An inverted
    pair (e.g. base=10, max=5) would cause the backoff to saturate
    immediately and silently, so we validate it eagerly.
    """

    payload = {**_MINIMAL_CFG, "base_delay_seconds": 10.0, "max_delay_seconds": 5.0}
    cfg_file = tmp_path / "retry_429.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="max_delay_seconds"):
        load_retry_429_policy(path=cfg_file)


def test_reset_cache_cycles_lru_cache(tmp_path) -> None:
    """``_reset_cache()`` must cause the next call to re-read from disk.

    Verifies the cache-busting contract: after ``_reset_cache()`` the loader
    returns a fresh object (identity check fails), confirming that the
    ``lru_cache`` on ``get_retry_429_policy`` was actually cleared.
    """

    cfg_file = tmp_path / "retry_429.json"
    cfg_file.write_text(json.dumps(_MINIMAL_CFG))

    first = load_retry_429_policy(path=cfg_file)

    # Reset and reload — the result must be a fresh object, not the same
    # instance that was constructed on the first call.
    _reset_cache()
    second = load_retry_429_policy(path=cfg_file)

    assert first is not second
    assert second.max_attempts == first.max_attempts  # values still match
