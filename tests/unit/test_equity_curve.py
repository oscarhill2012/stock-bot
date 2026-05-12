# tests/unit/test_equity_curve.py
"""compute_equity_curve reads portfolio_snapshots and anchors at first row."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from baselines.equity_curve import compute_equity_curve
from orchestrator.persistence import (
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


def _seed(db_url: str, rows: list[dict]) -> None:
    engine = make_engine(db_url)
    create_all(engine)
    Session = make_session_factory(engine)
    s = Session()
    for r in rows:
        save_portfolio_snapshot(s, r)
    s.commit()
    s.close()


def _row(tick_id: str, bot_value: float, spy_price: float, recorded_at=None):
    return {
        "tick_id": tick_id,
        "recorded_at": recorded_at or datetime.now(tz=UTC),
        "bot_total_value": bot_value,
        "bot_cash": bot_value,
        "bot_positions_value": 0.0,
        "bot_position_count": 0,
        "spy_price": spy_price,
        "spy_value_if_held": bot_value,
        "bot_return_pct": 0.0,
        "spy_return_pct": 0.0,
        "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    }


def test_empty_db_returns_empty_curve(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    engine = make_engine(db_url)
    create_all(engine)
    curve = compute_equity_curve(db_url)
    assert curve.timestamps == []
    assert curve.bot_pct == []
    assert curve.spy_pct == []


def test_single_row_anchor_only(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    _seed(db_url, [_row("init", 10000.0, 500.0)])
    curve = compute_equity_curve(db_url)
    assert len(curve.timestamps) == 1
    assert curve.bot_pct == [0.0]
    assert curve.spy_pct == [0.0]
    assert curve.excess_pct == [0.0]
    assert curve.anchor_tick_id == "init"
    assert curve.anchor_bot_value == 10000.0
    assert curve.anchor_spy_price == 500.0


def test_multi_row_anchored_correctly(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'a.db'}"
    _seed(db_url, [
        _row("init", 10000.0, 500.0),
        _row("tick-1", 10500.0, 510.0),  # bot +5%, spy +2%
        _row("tick-2", 10100.0, 525.0),  # bot +1%, spy +5%
    ])
    curve = compute_equity_curve(db_url)
    assert len(curve.timestamps) == 3
    assert curve.bot_pct[1] == pytest.approx(0.05)
    assert curve.spy_pct[1] == pytest.approx(0.02)
    assert curve.excess_pct[1] == pytest.approx(0.03)
    assert curve.bot_pct[2] == pytest.approx(0.01)
    assert curve.spy_pct[2] == pytest.approx(0.05)
    assert curve.excess_pct[2] == pytest.approx(-0.04)
