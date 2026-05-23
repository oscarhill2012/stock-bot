# tests/unit/test_session_service_factory.py
"""SessionService factory: accepts db_url; falls back to DATABASE_URL (Band 2).

The Band 1 dev/prod split has been replaced by a simple ``db_url`` argument
that callers supply directly — backtest passes a per-run sqlite URL, live
paths fall back to DATABASE_URL.  This file updates the pre-Band-2 tests to
match the new contract.
"""
from __future__ import annotations

import pytest

from orchestrator.persistence import make_session_service


def test_explicit_url_returns_database_session_service(monkeypatch) -> None:
    """An explicit ``db_url`` produces a ``DatabaseSessionService``."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    svc = make_session_service(db_url="sqlite+aiosqlite:///:memory:")

    assert svc.__class__.__name__ == "DatabaseSessionService"
    assert hasattr(svc, "db_engine")


def test_env_var_fallback_returns_database_session_service(monkeypatch) -> None:
    """When ``db_url`` is omitted, ``DATABASE_URL`` is used as a fallback."""
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    svc = make_session_service()

    assert svc.__class__.__name__ == "DatabaseSessionService"


def test_both_missing_raises_runtime_error(monkeypatch) -> None:
    """When neither ``db_url`` nor ``DATABASE_URL`` is present, a RuntimeError
    is raised rather than silently constructing a broken service.
    """
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        make_session_service()
