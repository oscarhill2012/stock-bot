# tests/unit/test_hard_reset.py
"""hard_reset archives all StockBot tables, truncates live, writes meta."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from lifecycle.hard_reset import hard_reset, ResetResult
from orchestrator.persistence import (
    BufferEntryRow,
    PortfolioSnapshotRow,
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


def _seed_live_db(db_url: str):
    engine = make_engine(db_url)
    create_all(engine)
    S = make_session_factory(engine)
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "init", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10000.0, "bot_cash": 10000.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 500.0, "spy_value_if_held": 10000.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()


def test_archive_creates_file_and_truncates_live(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    # Stub scheduler module — no real gcloud call
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)

    result = hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job=None,  # SQLite path: no scheduler
        meta_extra={"watchlist": ["AAPL"], "broker_mode": "paper", "starting_capital": 10000.0},
    )

    # Archive file written
    assert result.archive_path.exists()
    assert result.archive_path.suffix == ".db"
    # Meta file written next to archive
    meta_path = result.archive_path.with_suffix(".meta.json")
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text())
    assert meta["watchlist"] == ["AAPL"]
    assert meta["row_counts"]["portfolio_snapshots"] == 1

    # Live tables empty
    engine = make_engine(f"sqlite:///{db_path}")
    S = make_session_factory(engine)
    s = S()
    assert s.query(PortfolioSnapshotRow).count() == 0
    s.close()

    # Archive contains the row
    arc_engine = make_engine(f"sqlite:///{result.archive_path}")
    arc_S = make_session_factory(arc_engine)
    arc_s = arc_S()
    assert arc_s.query(PortfolioSnapshotRow).count() == 1
    arc_s.close()


def test_archive_path_collision_aborts(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"
    archive_dir.mkdir()

    # Pre-create a clashing archive file by running once
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)
    monkeypatch.setattr("lifecycle.hard_reset._timestamp", lambda: "FIXED")
    hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job=None,
        meta_extra={},
    )
    # Re-seed and try again with the same fixed timestamp → must abort
    _seed_live_db(f"sqlite:///{db_path}")
    with pytest.raises(FileExistsError):
        hard_reset(
            db_url=f"sqlite:///{db_path}",
            archive_dir=archive_dir,
            scheduler_job=None,
            meta_extra={},
        )


def test_pauses_scheduler_first_when_job_provided(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed_live_db(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    calls = []
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: calls.append(name))

    hard_reset(
        db_url=f"sqlite:///{db_path}",
        archive_dir=archive_dir,
        scheduler_job="stockbot-tick",
        meta_extra={},
    )
    assert calls == ["stockbot-tick"]
