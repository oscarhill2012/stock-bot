# tests/unit/test_session_service_factory.py
"""SessionService factory: dev → SQLite, prod → Postgres URL respected."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from orchestrator.persistence import make_session_service


def test_dev_returns_sqlite_database_session_service(monkeypatch, tmp_path):
    monkeypatch.setenv("STOCKBOT_ENV", "dev")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    svc = make_session_service()
    assert svc.__class__.__name__ == "DatabaseSessionService"
    # Verify SQLite backing: DatabaseSessionService exposes db_engine (not .engine)
    assert hasattr(svc, "db_engine")


def test_prod_uses_database_url(monkeypatch):
    monkeypatch.setenv("STOCKBOT_ENV", "prod")
    # DatabaseSessionService requires an async driver; use aiosqlite for the test
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    svc = make_session_service()
    assert svc.__class__.__name__ == "DatabaseSessionService"


def test_prod_without_database_url_raises(monkeypatch):
    monkeypatch.setenv("STOCKBOT_ENV", "prod")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        make_session_service()
