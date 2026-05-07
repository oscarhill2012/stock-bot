"""hard_reset — pause scheduler, archive all StockBot tables, truncate live tables."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import inspect, text

from orchestrator.persistence import Base, make_engine, make_session_factory

from . import scheduler

_STOCKBOT_TABLES = ("buffer_entries", "trade_log", "portfolio_snapshots", "attribution_signals")


@dataclass(frozen=True)
class ResetResult:
    archive_path: Path
    row_counts: dict[str, int]


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")


def _row_counts(db_url: str) -> dict[str, int]:
    engine = make_engine(db_url)
    counts: dict[str, int] = {}
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    Session = make_session_factory(engine)
    s = Session()
    try:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                counts[t] = s.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar_one()
    finally:
        s.close()
    return counts


def _archive_sqlite(src_url: str, archive_path: Path) -> None:
    src = src_url.replace("sqlite:///", "")
    if archive_path.exists():
        raise FileExistsError(f"archive already exists: {archive_path}")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    # VACUUM INTO copies the entire DB into a fresh file.
    conn = sqlite3.connect(src)
    try:
        conn.execute(f"VACUUM INTO '{archive_path.as_posix()}'")
        conn.commit()
    finally:
        conn.close()


def _archive_postgres(db_url: str, ts: str) -> str:
    """Create archive schema and copy each StockBot table into it."""
    engine = make_engine(db_url)
    schema = f"stockbot_archive_{ts.replace('-', '_').replace('T', '_')}"
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        for t in _STOCKBOT_TABLES:
            conn.execute(text(
                f'CREATE TABLE "{schema}"."{t}" AS SELECT * FROM public."{t}"'
            ))
    return schema


def _truncate_live(db_url: str) -> None:
    engine = make_engine(db_url)
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    with engine.begin() as conn:
        for t in _STOCKBOT_TABLES:
            if t in existing:
                conn.execute(text(f"DELETE FROM {t}"))


def hard_reset(
    *,
    db_url: str,
    archive_dir: Path,
    scheduler_job: str | None,
    meta_extra: dict[str, Any] | None = None,
) -> ResetResult:
    """Archive then truncate. Scheduler paused first if `scheduler_job` is set."""
    is_sqlite = db_url.startswith("sqlite")
    ts = _timestamp()
    archive_dir.mkdir(parents=True, exist_ok=True)

    # 1. Pause scheduler
    if scheduler_job:
        scheduler.pause_job(scheduler_job)

    # 2. Capture row counts BEFORE archive
    counts = _row_counts(db_url)

    # 3. Archive
    if is_sqlite:
        archive_path = archive_dir / f"{ts}.db"
        _archive_sqlite(db_url, archive_path)
    else:
        schema = _archive_postgres(db_url, ts)
        archive_path = archive_dir / f"{ts}.{schema}.txt"
        archive_path.write_text(f"archived to schema: {schema}\n")

    # 4. Truncate live tables
    _truncate_live(db_url)

    # 5. Write meta
    meta_path = archive_path.with_suffix(".meta.json")
    meta = {
        "archived_at": datetime.now(tz=timezone.utc).isoformat(),
        "db_url_kind": "sqlite" if is_sqlite else "postgres",
        "row_counts": counts,
        "scheduler_job": scheduler_job,
        **(meta_extra or {}),
    }
    meta_path.write_text(json.dumps(meta, indent=2, default=str))

    return ResetResult(archive_path=archive_path, row_counts=counts)
