"""set_active_provider must refuse unknown provider names.

Audit A-041: the runtime swap previously accepted any string, only failing at
the next dispatch with a confusing KeyError. The contract is: a swap target
must be a registered ``(domain, name)`` pair. Anything else is a typo and we
fail loudly, immediately.
"""
from __future__ import annotations

import pytest

# Import the providers package so the @register decorators populate the
# registry before we exercise set_active_provider.
import data.providers  # noqa: F401 — import-for-side-effects
from data.registry import set_active_provider


def test_set_active_provider_raises_on_unknown_provider_name():
    """Unknown provider name on a known domain → ValueError, no swap applied."""

    with pytest.raises(ValueError, match="no provider registered"):
        set_active_provider("news", "nonexistent_provider_xyz")


def test_set_active_provider_raises_on_unknown_domain():
    """Unknown domain still raises (pre-existing behaviour, kept)."""

    with pytest.raises(ValueError, match="unknown domain"):
        set_active_provider("not_a_real_domain", "anything")


def test_set_active_provider_accepts_registered_pair_and_restores():
    """The happy path still works — swap to a registered provider, restore."""

    # Pick a domain that has a single registered provider (post-cull) and
    # swap it to itself; the restore callable must put the original back.
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["price_history"]

    restore = set_active_provider("price_history", original)

    assert cfg.providers["price_history"] == original

    restore()

    assert cfg.providers["price_history"] == original
