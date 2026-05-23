# tests/unit/orchestrator/test_persistence.py
"""Unit tests for ``make_session_service()`` factory.

Covers the three resolution branches introduced in Band 2:
1. Explicit ``db_url`` argument wins.
2. Falls back to ``DATABASE_URL`` environment variable when no argument is given.
3. Raises ``RuntimeError`` when neither is available.
"""
from __future__ import annotations

import pytest

from orchestrator.persistence import make_session_service


def test_explicit_db_url_is_used(monkeypatch) -> None:
    """An explicit ``db_url`` argument results in a ``DatabaseSessionService``
    backed by that URL, regardless of what the environment says.
    """
    # Clear the env var so we know the explicit argument is the only source.
    monkeypatch.delenv("DATABASE_URL", raising=False)

    svc = make_session_service(db_url="sqlite+aiosqlite:///:memory:")

    assert svc.__class__.__name__ == "DatabaseSessionService"
    # DatabaseSessionService exposes ``db_engine`` (async engine).
    assert hasattr(svc, "db_engine")


def test_env_var_fallback_used_when_no_arg(monkeypatch) -> None:
    """When ``db_url`` is omitted, the factory falls back to ``DATABASE_URL``."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    svc = make_session_service()

    assert svc.__class__.__name__ == "DatabaseSessionService"


def test_raises_when_neither_db_url_nor_env_var(monkeypatch) -> None:
    """When both ``db_url`` and ``DATABASE_URL`` are absent, a ``RuntimeError``
    is raised with a helpful message rather than producing a silently broken
    service instance.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="make_session_service"):
        make_session_service()
