"""Unit test: CachedDataStore must raise RuntimeError on schema-version mismatch.

If an existing SQLite file was written by an older (or newer) version of the
cache schema, instantiating ``CachedDataStore`` over it should immediately
raise ``RuntimeError`` rather than silently accepting the stale file and
producing broken results.

This test seeds a fresh DB with a ``MetaRow(schema_version=0)`` via raw SQL,
then attempts to open it with ``CachedDataStore`` and asserts the guard fires.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from backtest.cache.schema import SCHEMA_VERSION, create_all
from backtest.cache.store import CachedDataStore


def _seed_wrong_version(db_path: Path, wrong_version: int) -> None:
    """Create the full schema then overwrite the meta row with a stale version.

    Parameters
    ----------
    db_path:
        Path at which to create (or overwrite) the SQLite file.
    wrong_version:
        The ``schema_version`` value to inject into the ``meta`` table.
    """
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    # Lay down all tables so the schema is structurally valid.
    create_all(engine)

    with engine.begin() as conn:
        # Remove any existing meta row, then insert one with the stale version.
        conn.execute(text("DELETE FROM meta"))
        conn.execute(
            text("INSERT INTO meta (schema_version, created_at) VALUES (:v, datetime('now'))"),
            {"v": wrong_version},
        )

    engine.dispose()


def test_schema_version_mismatch_raises(tmp_path: Path) -> None:
    """Opening a cache whose schema_version != SCHEMA_VERSION must raise.

    The error message must name both the file version and the expected version
    so the user knows exactly what has gone wrong.
    """
    db_path = tmp_path / "stale_cache.sqlite"

    # Use version 0, which will never equal a real SCHEMA_VERSION (currently 2).
    stale_version = 0
    assert stale_version != SCHEMA_VERSION, (
        "Test precondition broken: stale_version must differ from SCHEMA_VERSION"
    )

    _seed_wrong_version(db_path, stale_version)

    with pytest.raises(RuntimeError) as exc_info:
        CachedDataStore(db_path)

    error_message = str(exc_info.value)

    # The error must mention both versions so the user can diagnose the gap.
    assert str(stale_version) in error_message, (
        f"RuntimeError should mention the stale version ({stale_version}), "
        f"got: {error_message!r}"
    )
    assert str(SCHEMA_VERSION) in error_message, (
        f"RuntimeError should mention the expected version ({SCHEMA_VERSION}), "
        f"got: {error_message!r}"
    )


def test_matching_schema_version_does_not_raise(tmp_path: Path) -> None:
    """Sanity check: a fresh cache (correct version) must not raise.

    This guards against the mismatch check being too aggressive and rejecting
    a legitimately fresh file.
    """
    db_path = tmp_path / "fresh_cache.sqlite"
    # Must not raise.
    store = CachedDataStore(db_path)
    assert store is not None
