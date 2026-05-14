"""Unit tests for the auto-derived prompt-version fingerprint (B23).

The helper is intentionally pure (string -> string).  The module-level
constants are computed at import time from the real rendered prompts —
so the tests cover (a) the helper's algebraic properties and (b) the
sanity of the live constants.
"""
from __future__ import annotations

from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
    NEWS_PROMPT_VERSION,
    _derive_prompt_version,
)

# ---------------------------------------------------------------------------
# Helper behaviour
# ---------------------------------------------------------------------------

def test_derive_prompt_version_is_deterministic():
    """Calling the helper twice on the same string returns the same digest."""
    s = "You are the News analyst. catalysts: ['earnings']..."
    assert _derive_prompt_version(s) == _derive_prompt_version(s)


def test_derive_prompt_version_differs_on_input_change():
    """A one-character change in the instruction changes the digest."""
    a = "You are the News analyst."
    b = "You are the News analyst!"
    assert _derive_prompt_version(a) != _derive_prompt_version(b)


def test_derive_prompt_version_has_auto_prefix():
    """The returned string is prefixed with ``auto:`` so a cache reader can
    tell at a glance that the version is machine-derived rather than a
    hand-set date string.
    """
    out = _derive_prompt_version("anything")
    assert out.startswith("auto:")


def test_derive_prompt_version_digest_length():
    """The digest portion is 12 hex chars (6-byte blake2b)."""
    out = _derive_prompt_version("anything")
    _, digest = out.split(":", 1)
    assert len(digest) == 12
    # All hex digits.
    int(digest, 16)


# ---------------------------------------------------------------------------
# Live constants
# ---------------------------------------------------------------------------

def test_live_news_prompt_version_is_auto_derived():
    """The module-level News version constant is computed by the helper."""
    assert NEWS_PROMPT_VERSION.startswith("auto:")


def test_live_fundamental_prompt_version_is_auto_derived():
    """The module-level Fundamental version constant is computed by the helper."""
    assert FUNDAMENTAL_PROMPT_VERSION.startswith("auto:")


def test_news_and_fundamental_versions_differ():
    """The two analysts have distinct rendered prompts, so they must have
    distinct version fingerprints — otherwise a cache cross-contamination
    risk exists between the two analyst sub-trees.
    """
    assert NEWS_PROMPT_VERSION != FUNDAMENTAL_PROMPT_VERSION
