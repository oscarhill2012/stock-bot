# tests/unit/test_init_db_script.py
"""init_db creates *all* StockBot ORM tables, and `_STOCKBOT_TABLES`
matches `Base.metadata` exactly (A-011 regression).

Historically this test hand-listed three table names and silently
agreed with the buggy lifecycle tuple.  Plan 04 derives the expected
set from ``Base.metadata.tables.keys()`` directly so a future ORM
table can never silently fall out of preflight / hard_reset coverage.
"""
from __future__ import annotations

from sqlalchemy import inspect

from lifecycle._tables import STOCKBOT_TABLES
from orchestrator.persistence import Base, make_engine
from scripts.init_db import init_db


def test_stockbot_tables_set_matches_orm_metadata_exactly() -> None:
    """The lifecycle table set MUST equal ``Base.metadata.tables.keys()``
    — any drift means preflight / hard_reset silently misses an ORM table."""

    assert set(STOCKBOT_TABLES) == set(Base.metadata.tables.keys()), (
        f"STOCKBOT_TABLES drifted from Base.metadata: "
        f"only in tuple = {set(STOCKBOT_TABLES) - set(Base.metadata.tables.keys())}; "
        f"only in metadata = {set(Base.metadata.tables.keys()) - set(STOCKBOT_TABLES)}"
    )


def test_init_db_creates_every_orm_table(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    # The script must create every ORM table — derived expectation,
    # not a hand-maintained literal.
    assert set(Base.metadata.tables.keys()).issubset(tables)


def test_init_db_is_idempotent(tmp_path) -> None:
    db_path = tmp_path / "test.db"
    init_db(f"sqlite:///{db_path}")
    init_db(f"sqlite:///{db_path}")  # second run must not raise
    engine = make_engine(f"sqlite:///{db_path}")
    tables = set(inspect(engine).get_table_names())
    assert set(Base.metadata.tables.keys()).issubset(tables)
