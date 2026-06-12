# tests/unit/test_init_db_script.py
"""init_db creates *all* StockBot ORM tables, and `_STOCKBOT_TABLES`
matches `Base.metadata` exactly (A-011 regression).

Historically this test hand-listed three table names and silently
agreed with the buggy lifecycle tuple.  Plan 04 derives the expected
set from ``Base.metadata.tables.keys()`` directly so a future ORM
table can never silently fall out of preflight / hard_reset coverage.
"""
from __future__ import annotations

import pytest
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


def test_check_live_tables_empty_rejects_non_default_schema():
    """Postgres non-public schema is not supported — document via explicit raise.

    The lifecycle helper hard-codes un-qualified table references, which resolve
    against the Postgres `search_path` (defaulting to `public`). A URL that pins
    a different schema would silently query the wrong tables.  A-091 mandates a
    loud failure with a documented migration path instead.
    """
    from lifecycle.initialise import UnsupportedSchemaError, _check_live_tables_empty

    # URL with an explicit search_path that is NOT public — simulates a
    # multi-tenant deployment attempting to reuse the lifecycle helper.
    url = "postgresql+psycopg://u:p@h/db?options=-csearch_path%3Dtenant_a"

    with pytest.raises(UnsupportedSchemaError, match="public"):
        _check_live_tables_empty(url)


def test_check_live_tables_empty_rejects_schema_containing_public_substring():
    """A schema like 'notpublic' must be rejected, not pass on substring match.

    Regression for the A-091 review finding: the original guard used a plain
    ``"public" in ...`` substring test, which allowed values such as
    ``notpublic``, ``public_tenant``, and ``public2`` through incorrectly.
    The tightened regex anchors on an exact word boundary so only the literal
    value ``public`` is accepted.
    """
    from lifecycle.initialise import UnsupportedSchemaError, _check_live_tables_empty

    url = "postgresql+psycopg://u:p@h/db?options=-csearch_path%3Dnotpublic"

    with pytest.raises(UnsupportedSchemaError, match="public"):
        _check_live_tables_empty(url)


def test_check_live_tables_empty_accepts_public_schema():
    """An explicit search_path=public must pass the schema guard.

    The guard should only fire for non-public schemas.  A URL that explicitly
    pins ``search_path=public`` (or the percent-encoded equivalent) is the
    default case and must be allowed through to the engine-creation stage.
    Any error raised beyond that point (e.g. a real connection error) is
    irrelevant — what matters is that ``UnsupportedSchemaError`` is NOT raised.
    """
    from lifecycle.initialise import UnsupportedSchemaError, _check_live_tables_empty

    url = "postgresql+psycopg://u:p@h/db?options=-csearch_path%3Dpublic"

    # The guard passes and the function proceeds to connect — that connection
    # will fail in this test environment (no real Postgres), but as long as the
    # error raised is NOT UnsupportedSchemaError the guard itself is correct.
    try:
        _check_live_tables_empty(url)
    except UnsupportedSchemaError:
        pytest.fail(
            "UnsupportedSchemaError raised for search_path=public; guard is too strict"
        )
    except Exception:
        # Any other exception (connection refused, etc.) is expected and fine.
        pass
