# tests/unit/test_init_db_script.py
"""init_db creates all StockBot tables, idempotent."""
from __future__ import annotations

from sqlalchemy import inspect

from orchestrator.persistence import make_engine
from scripts.init_db import init_db

EXPECTED_TABLES = {"trade_log", "portfolio_snapshots"}


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    init_db(f"sqlite:///{db_path}")  # second run must not raise
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES.issubset(tables)
