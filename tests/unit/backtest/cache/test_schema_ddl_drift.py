"""Tests for cache DDL-drift detection and additive-column auto-migration.

Two concerns are tested here:

1. **DDL drift guard (Part B)** — pins a snapshot of the current schema DDL so
   that any future column add/drop/retype fails loudly.  When the test fails,
   the developer must either update the snapshot (for purely additive nullable
   changes — the runtime auto-migration handles existing files) or also bump
   ``SCHEMA_VERSION`` (for breaking changes).

2. **Additive migration regression (Part C)** — reproduces the original bug
   shape: a ``filings`` table built *without* ``period_of_report``, followed by
   opening a ``CachedDataStore`` over it.  Asserts the column is added without
   data loss, and that a brand-new store also works correctly.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect

from backtest.cache.schema import (
    SCHEMA_VERSION,
    CacheBase,
    create_all,
)
from backtest.cache.store import CachedDataStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_ddl_snapshot() -> list[tuple[str, str, str, bool, bool]]:
    """Return a canonical, sorted snapshot of the current SQLAlchemy schema.

    For every table in ``CacheBase.metadata.sorted_tables``, every column is
    represented as a 5-tuple:

        (table_name, column_name, sqlite_type, nullable, primary_key)

    ``sqlite_type`` is rendered via SQLAlchemy's SQLite dialect so the
    representation matches what ``CREATE TABLE`` would produce.

    Returns
    -------
    list[tuple[str, str, str, bool, bool]]
        Rows sorted by ``(table_name, column_name)`` for a stable comparison.
    """
    dialect = sqlite_dialect.dialect()
    rows: list[tuple[str, str, str, bool, bool]] = []

    for table in CacheBase.metadata.sorted_tables:
        for col in table.columns:
            col_type_str = col.type.compile(dialect=dialect)
            rows.append((
                table.name,
                col.name,
                col_type_str,
                bool(col.nullable),
                bool(col.primary_key),
            ))

    return sorted(rows)


# ---------------------------------------------------------------------------
# Part B — DDL drift guard
# ---------------------------------------------------------------------------

# Pinned snapshot of the current schema (as of Phase 14, period_of_report and
# litigation_excerpt included).  ``litigation_excerpt`` was added to the
# ``filings`` table in Phase 14 (Plan 1, Task 3) as an additive nullable TEXT
# column — no SCHEMA_VERSION bump was needed, as the runtime auto-migration in
# CachedDataStore._migrate_additive_columns backfills existing cache files.  If
# this test fails after a DDL change, update this snapshot AND read the
# assertion message to decide whether a SCHEMA_VERSION bump is also required.
_PINNED_DDL_SNAPSHOT: list[tuple[str, str, str, bool, bool]] = [
    # cache_runs table
    ("cache_runs", "domain",          "VARCHAR", True,  False),
    ("cache_runs", "error",           "VARCHAR", True,  False),
    ("cache_runs", "finished_at",     "DATETIME", True,  False),
    ("cache_runs", "rows_written",    "INTEGER",  True,  False),
    ("cache_runs", "run_id",          "VARCHAR", False, True),
    ("cache_runs", "source_provider", "VARCHAR", True,  False),
    ("cache_runs", "started_at",      "DATETIME", True,  False),
    ("cache_runs", "status",          "VARCHAR", True,  False),
    ("cache_runs", "ticker",          "VARCHAR", True,  False),
    ("cache_runs", "window_key",      "VARCHAR", True,  False),
    # company_ratios table
    ("company_ratios", "as_of_date",             "DATE",    False, True),
    ("company_ratios", "beta",                    "FLOAT",   True,  False),
    ("company_ratios", "debt_to_equity",          "FLOAT",   True,  False),
    ("company_ratios", "dividend_yield",          "FLOAT",   True,  False),
    ("company_ratios", "fifty_day_average",       "FLOAT",   True,  False),
    ("company_ratios", "forward_pe",              "FLOAT",   True,  False),
    ("company_ratios", "free_cash_flow",          "FLOAT",   True,  False),
    ("company_ratios", "last_price",              "FLOAT",   True,  False),
    ("company_ratios", "long_name",               "VARCHAR", True,  False),
    ("company_ratios", "market_cap",              "FLOAT",   True,  False),
    ("company_ratios", "peg",                     "FLOAT",   True,  False),
    ("company_ratios", "profit_margin",           "FLOAT",   True,  False),
    ("company_ratios", "revenue_growth_yoy",      "FLOAT",   True,  False),
    ("company_ratios", "roe",                     "FLOAT",   True,  False),
    ("company_ratios", "sector",                  "VARCHAR", True,  False),
    ("company_ratios", "ticker",                  "VARCHAR", False, True),
    ("company_ratios", "trailing_pe",             "FLOAT",   True,  False),
    ("company_ratios", "two_hundred_day_average", "FLOAT",   True,  False),
    # filings table
    ("filings", "accession_no",         "VARCHAR", False, True),
    ("filings", "filed_at",             "DATETIME", True,  False),
    ("filings", "form_type",            "VARCHAR", True,  False),
    ("filings", "litigation_excerpt",   "TEXT",    True,  False),
    ("filings", "mda_excerpt",          "TEXT",    True,  False),
    ("filings", "period_of_report",     "TEXT",    True,  False),
    ("filings", "risk_factors_excerpt", "TEXT",    True,  False),
    ("filings", "ticker",               "VARCHAR", True,  False),
    ("filings", "title",                "VARCHAR", True,  False),
    ("filings", "url",                  "VARCHAR", True,  False),
    # insider_trades table
    ("insider_trades", "filed_at",         "DATETIME", True,  False),
    ("insider_trades", "footnote",         "TEXT",    True,  False),
    ("insider_trades", "form_type",        "VARCHAR", True,  False),
    ("insider_trades", "insider_name",     "VARCHAR", True,  False),
    ("insider_trades", "insider_title",    "VARCHAR", True,  False),
    ("insider_trades", "is_10b5_1",        "BOOLEAN", True,  False),
    ("insider_trades", "price_per_share",  "FLOAT",   True,  False),
    ("insider_trades", "row_hash",         "VARCHAR", False, True),
    ("insider_trades", "shares",           "FLOAT",   True,  False),
    ("insider_trades", "side",             "VARCHAR", True,  False),
    ("insider_trades", "ticker",           "VARCHAR", True,  False),
    ("insider_trades", "transaction_code", "VARCHAR", True,  False),
    ("insider_trades", "transaction_date", "DATE",    True,  False),
    # meta table
    ("meta", "created_at",     "DATETIME", True,  False),
    ("meta", "schema_version", "INTEGER",  False, True),
    # news_articles table
    ("news_articles", "headline",     "VARCHAR", True,  False),
    ("news_articles", "published_at", "DATETIME", True,  False),
    ("news_articles", "sentiment",    "FLOAT",   True,  False),
    ("news_articles", "source",       "VARCHAR", True,  False),
    ("news_articles", "summary",      "VARCHAR", True,  False),
    ("news_articles", "ticker",       "VARCHAR", False, True),
    ("news_articles", "url",          "VARCHAR", False, True),
    # notable_holders table
    ("notable_holders", "accession_no",  "VARCHAR", False, True),
    ("notable_holders", "filed_at",      "DATETIME", True,  False),
    ("notable_holders", "form_type",     "VARCHAR", True,  False),
    ("notable_holders", "holder",        "VARCHAR", True,  False),
    ("notable_holders", "intent",        "VARCHAR", True,  False),
    ("notable_holders", "is_amendment",  "BOOLEAN", True,  False),
    ("notable_holders", "ticker",        "VARCHAR", True,  False),
    ("notable_holders", "url",           "VARCHAR", True,  False),
    # ohlcv_bars table
    ("ohlcv_bars", "close",  "FLOAT",    True,  False),
    ("ohlcv_bars", "high",   "FLOAT",    True,  False),
    ("ohlcv_bars", "low",    "FLOAT",    True,  False),
    ("ohlcv_bars", "open",   "FLOAT",    True,  False),
    ("ohlcv_bars", "ticker", "VARCHAR",  False, True),
    ("ohlcv_bars", "ts",     "DATETIME", False, True),
    ("ohlcv_bars", "volume", "FLOAT",    True,  False),
    # politician_trades table
    ("politician_trades", "amount_max_usd",   "FLOAT",    True,  False),
    ("politician_trades", "amount_min_usd",   "FLOAT",    True,  False),
    ("politician_trades", "chamber",          "VARCHAR",  True,  False),
    ("politician_trades", "disclosure_date",  "DATETIME", True,  False),
    ("politician_trades", "party",            "VARCHAR",  True,  False),
    ("politician_trades", "politician",       "VARCHAR",  True,  False),
    ("politician_trades", "row_hash",         "VARCHAR",  False, True),
    ("politician_trades", "side",             "VARCHAR",  True,  False),
    ("politician_trades", "ticker",           "VARCHAR",  True,  False),
    ("politician_trades", "transaction_date", "DATETIME", True,  False),
]


def test_ddl_snapshot_matches_live_schema() -> None:
    """The live ORM schema must exactly match the pinned DDL snapshot.

    If this test fails, read the assertion message — it explains what to do
    depending on the nature of the change.
    """
    live = _build_ddl_snapshot()

    assert live == _PINNED_DDL_SNAPSHOT, (
        "The cache DDL changed.\n\n"
        "  * If the change is purely additive (new nullable columns only): "
        "update the _PINNED_DDL_SNAPSHOT constant in this test file — the "
        "runtime auto-migration in CachedDataStore._migrate_additive_columns "
        "handles existing cache files automatically.\n\n"
        "  * If the change is breaking (column dropped / retyped / renamed, "
        "or a new NOT-NULL / primary-key column added): ALSO bump "
        "SCHEMA_VERSION in src/backtest/cache/schema.py so that existing "
        "cache files are forced to rebuild rather than silently producing "
        "corrupt results.\n\n"
        f"Live schema rows:   {live}\n"
        f"Pinned schema rows: {_PINNED_DDL_SNAPSHOT}"
    )


# ---------------------------------------------------------------------------
# Part C — additive migration regression test
# ---------------------------------------------------------------------------

_LEGACY_FILINGS_DDL = """
    CREATE TABLE filings (
        accession_no         VARCHAR NOT NULL PRIMARY KEY,
        ticker               VARCHAR,
        form_type            VARCHAR,
        filed_at             DATETIME,
        title                VARCHAR,
        url                  VARCHAR,
        risk_factors_excerpt TEXT,
        mda_excerpt          TEXT
    )
"""
# Note: ``period_of_report`` is deliberately absent — this is the pre-Phase 13
# schema that triggers the silent OperationalError bug.


def _build_legacy_filings_db(db_path: Path) -> None:
    """Create a SQLite file with a ``filings`` table that lacks ``period_of_report``.

    Also creates all other cache tables in their current form (via ``create_all``
    with the ``filings`` table created manually first to avoid the ORM version
    overwriting it).  The approach:

    1. Create all non-filings tables via a temporary engine with ``checkfirst``
       behaviour — but since SQLAlchemy's ``create_all`` skips existing tables,
       we can pre-create the trimmed filings table and then call ``create_all``
       which will leave it alone.
    2. Pre-populate the legacy ``filings`` table with one representative row so
       the migration test can confirm the row survives after ``period_of_report``
       is added.

    Parameters
    ----------
    db_path:
        Path at which to create the legacy SQLite file.
    """
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    with engine.begin() as conn:
        # Create the trimmed filings table first (lacking period_of_report).
        conn.execute(text(_LEGACY_FILINGS_DDL))

        # Insert a representative row to verify data is preserved post-migration.
        conn.execute(text(
            "INSERT INTO filings "
            "(accession_no, ticker, form_type, filed_at, title, url, "
            " risk_factors_excerpt, mda_excerpt) "
            "VALUES "
            "('0001234567-23-000001', 'AAPL', '10-K', "
            " '2023-11-03 00:00:00', 'Annual Report', "
            " 'https://sec.gov/Archives/test', "
            " 'Risk factors text.', 'MD&A text.')"
        ))

    # Now run create_all — SQLAlchemy skips tables that already exist, so the
    # trimmed ``filings`` table is preserved while all other tables are created.
    create_all(engine)

    engine.dispose()


def test_additive_migration_adds_period_of_report(tmp_path: Path) -> None:
    """Opening a store over a legacy cache must add ``period_of_report`` without data loss.

    Reproduces the original bug: a filings table built without
    ``period_of_report`` caused ``sqlite3.OperationalError: table filings has
    no column named period_of_report`` at write time.  After
    ``_migrate_additive_columns`` runs, the column must exist and the
    pre-existing row must still be present.
    """
    db_path = tmp_path / "legacy_cache.sqlite"

    # Build a cache file that resembles a pre-Phase-13 store (no period_of_report).
    _build_legacy_filings_db(db_path)

    # Sanity-check: the column is genuinely absent before migration.
    pre_engine = create_engine(f"sqlite:///{db_path}", future=True)
    pre_cols = {c["name"] for c in inspect(pre_engine).get_columns("filings")}
    pre_engine.dispose()
    assert "period_of_report" not in pre_cols, (
        "Test precondition failed: period_of_report should be absent before "
        "opening CachedDataStore"
    )

    # Opening CachedDataStore must trigger the additive migration.
    store = CachedDataStore(db_path)

    # Verify the column was added.
    post_cols = {c["name"] for c in inspect(store._engine).get_columns("filings")}
    assert "period_of_report" in post_cols, (
        "period_of_report column was not added by _migrate_additive_columns"
    )

    # Verify the pre-existing row is still present (no data loss).
    with store._engine.connect() as conn:
        rows = conn.execute(
            text("SELECT accession_no FROM filings")
        ).fetchall()

    accession_nos = [r[0] for r in rows]
    assert "0001234567-23-000001" in accession_nos, (
        f"Pre-existing filings row was lost after migration; "
        f"found accession_nos: {accession_nos}"
    )

    # Verify the migrated column defaults to NULL for the old row.
    with store._engine.connect() as conn:
        period = conn.execute(
            text(
                "SELECT period_of_report FROM filings "
                "WHERE accession_no = '0001234567-23-000001'"
            )
        ).scalar_one()

    assert period is None, (
        f"Expected period_of_report to be NULL for legacy row, got {period!r}"
    )


def test_fresh_store_builds_with_period_of_report(tmp_path: Path) -> None:
    """A brand-new CachedDataStore must include period_of_report from the start.

    Guards the normal (non-migration) path — fresh caches should be built with
    the current full schema via ``create_all`` without needing the ALTER path.
    """
    db_path = tmp_path / "fresh_cache.sqlite"

    store = CachedDataStore(db_path)

    # Column must exist on a freshly created store.
    cols = {c["name"] for c in inspect(store._engine).get_columns("filings")}
    assert "period_of_report" in cols, (
        "period_of_report column missing from a freshly created CachedDataStore"
    )

    # Schema version must be stamped correctly.
    with store._engine.connect() as conn:
        version = conn.execute(
            text("SELECT schema_version FROM meta")
        ).scalar_one()

    assert version == SCHEMA_VERSION, (
        f"Expected schema_version={SCHEMA_VERSION}, got {version}"
    )
