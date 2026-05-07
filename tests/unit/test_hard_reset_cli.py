# tests/unit/test_hard_reset_cli.py
"""hard_reset CLI: literal-RESET confirmation, --yes flag for tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from orchestrator.persistence import create_all, make_engine, make_session_factory, save_portfolio_snapshot
from scripts import hard_reset as cli


def _seed(db_url: str):
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


def test_yes_flag_skips_prompt(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "live.db"
    _seed(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"

    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "pause_job", lambda name: None)

    cli.main([
        "--db-url", f"sqlite:///{db_path}",
        "--archive-dir", str(archive_dir),
        "--yes",
    ])
    out = capsys.readouterr().out
    assert "Archived" in out


def test_wrong_confirmation_aborts(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    _seed(f"sqlite:///{db_path}")
    archive_dir = tmp_path / "archives"
    monkeypatch.setattr("builtins.input", lambda _: "nope")

    with pytest.raises(SystemExit):
        cli.main([
            "--db-url", f"sqlite:///{db_path}",
            "--archive-dir", str(archive_dir),
        ])
