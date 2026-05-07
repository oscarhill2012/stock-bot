# tests/unit/test_plot_equity.py
"""plot_equity renders a non-empty PNG and exits 0 with a normal DB."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from orchestrator.persistence import create_all, make_engine, make_session_factory, save_portfolio_snapshot
from scripts.plot_equity import render


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
    save_portfolio_snapshot(s, {
        "tick_id": "tick-1", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 10500.0, "bot_cash": 10500.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 510.0, "spy_value_if_held": 10200.0,
        "bot_return_pct": 5.0, "spy_return_pct": 2.0, "excess_return_pct": 3.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()


def test_render_writes_non_empty_png(tmp_path):
    db_path = tmp_path / "x.db"
    db_url = f"sqlite:///{db_path}"
    _seed(db_url)
    out = tmp_path / "plot.png"
    render(db_url=db_url, out_path=out)
    assert out.exists()
    assert out.stat().st_size > 1000  # > 1 KB sanity


def test_render_empty_db_writes_empty_message_png(tmp_path):
    db_path = tmp_path / "x.db"
    db_url = f"sqlite:///{db_path}"
    engine = make_engine(db_url)
    create_all(engine)
    out = tmp_path / "plot.png"
    render(db_url=db_url, out_path=out)
    assert out.exists()  # rendered "no data yet" placeholder
