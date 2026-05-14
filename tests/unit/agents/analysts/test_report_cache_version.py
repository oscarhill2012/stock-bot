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


# ---------------------------------------------------------------------------
# Template-edit regression tests
# ---------------------------------------------------------------------------
# These two tests pin the silent-stale-cache failure mode that B23 was
# designed to close: if a contributor edits a prompt template without
# bumping a hand-maintained version string, old cached verdicts would be
# served silently under the wrong prompt.  Auto-derivation means any edit
# to the rendered instruction — template body, closed-vocab list, or
# char-cap value — automatically changes the digest, making the old cache
# entry a miss.
#
# We verify ``_derive_prompt_version`` is sensitive to rendered-string
# changes by:
#   1. Rendering the live instruction.
#   2. Mutating the rendered string (appending a marker).
#   3. Asserting the two digests differ.
#
# We do NOT patch the prompt module's source — string-level mutation of
# the rendered output is sufficient to prove sensitivity without
# monkeypatching.
# ---------------------------------------------------------------------------

def test_news_template_edit_changes_version():
    """Simulates a prompt-template edit: any mutation to the rendered News
    instruction must produce a different version fingerprint.

    Pins the silent-stale-cache failure mode — ensures ``_derive_prompt_version``
    is actually sensitive to downstream renderer output.
    """
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.news.prompts import build_news_instruction

    heuristics = load_heuristics()

    # Render the live instruction as it stands.
    rendered = build_news_instruction(heuristics.news_vocabulary)
    digest_before = _derive_prompt_version(rendered)

    # Simulate a template edit by appending an arbitrary marker to the rendered
    # string — equivalent to a contributor adding a sentence to the template.
    mutated = rendered + "\n# EDIT MARKER"
    digest_after = _derive_prompt_version(mutated)

    assert digest_before != digest_after, (
        "Version fingerprint did not change after simulated template edit — "
        "the silent-stale-cache failure mode is not closed."
    )


def test_fundamental_template_edit_changes_version():
    """Simulates a prompt-template edit: any mutation to the rendered
    Fundamental instruction must produce a different version fingerprint.

    Pins the silent-stale-cache failure mode — ensures ``_derive_prompt_version``
    is actually sensitive to downstream renderer output.
    """
    from agents.analysts.fundamental.prompts import build_fundamental_instruction
    from agents.analysts.heuristics import load_heuristics

    heuristics = load_heuristics()

    # Render the live instruction as it stands.
    rendered = build_fundamental_instruction(heuristics.fundamental_vocabulary)
    digest_before = _derive_prompt_version(rendered)

    # Simulate a template edit by appending an arbitrary marker to the rendered
    # string — equivalent to a contributor adding a sentence to the template.
    mutated = rendered + "\n# EDIT MARKER"
    digest_after = _derive_prompt_version(mutated)

    assert digest_before != digest_after, (
        "Version fingerprint did not change after simulated template edit — "
        "the silent-stale-cache failure mode is not closed."
    )
