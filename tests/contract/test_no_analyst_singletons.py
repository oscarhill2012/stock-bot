"""Tripwire — no module-level deterministic-analyst singletons.

Singletons executed file I/O at import time (``load_heuristics()`` reads a
JSON config) and any failure produced a hard-to-trace ImportError far from
the misconfiguration.  Audit A-074 mandates factories only.
"""
from __future__ import annotations

from agents.analysts.social.agent import _build_social_analyst
from agents.analysts.technical.agent import _build_technical_analyst


def test_technical_module_does_not_expose_singleton():
    """Importing the agent module must not bind ``technical_analyst``."""
    from agents.analysts.technical import agent as tech_mod
    assert not hasattr(tech_mod, "technical_analyst"), (
        "technical_analyst singleton was deleted in Plan 09 — use "
        "_build_technical_analyst() instead."
    )


def test_social_module_does_not_expose_singleton():
    """Importing the agent module must not bind ``social_analyst``."""
    from agents.analysts.social import agent as soc_mod
    assert not hasattr(soc_mod, "social_analyst"), (
        "social_analyst singleton was deleted in Plan 09 — use "
        "_build_social_analyst() instead."
    )


def test_technical_package_init_does_not_reexport_singleton():
    """The technical package ``__init__`` was a 2-line re-export — now docstring-only."""
    from agents.analysts import technical
    assert not hasattr(technical, "technical_analyst")


def test_build_technical_analyst_returns_a_fresh_instance_each_call():
    """Factory contract — successive calls return distinct objects (no hidden cache)."""
    a = _build_technical_analyst()
    b = _build_technical_analyst()
    assert a is not b, "factory must build fresh instances, not memoise"
    assert type(a) is type(b)


def test_build_social_analyst_returns_a_fresh_instance_each_call():
    """Factory contract — successive calls return distinct objects (no hidden cache)."""
    a = _build_social_analyst()
    b = _build_social_analyst()
    assert a is not b
    assert type(a) is type(b)
