"""Bot-vs-SPY equity curve from portfolio_snapshots. Shared with future dashboard."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from orchestrator.persistence import (
    PortfolioSnapshotRow,
    make_engine,
    make_session_factory,
)


@dataclass(frozen=True)
class EquityCurve:
    timestamps: list[datetime] = field(default_factory=list)
    bot_pct: list[float] = field(default_factory=list)
    spy_pct: list[float] = field(default_factory=list)
    excess_pct: list[float] = field(default_factory=list)
    anchor_tick_id: str | None = None
    anchor_bot_value: float | None = None
    anchor_spy_price: float | None = None


def compute_equity_curve(db_url: str) -> EquityCurve:
    """Read portfolio_snapshots ordered by id; anchor on first row.

    Empty DB → empty curve. Single row → all-zero pcts at length 1.
    """
    engine = make_engine(db_url)
    Session = make_session_factory(engine)
    s = Session()
    try:
        rows = s.query(PortfolioSnapshotRow).order_by(PortfolioSnapshotRow.id).all()
        if not rows:
            return EquityCurve()
        anchor = rows[0]
        timestamps: list[datetime] = []
        bot_pct: list[float] = []
        spy_pct: list[float] = []
        excess_pct: list[float] = []
        for r in rows:
            timestamps.append(r.recorded_at)
            bp = (r.bot_total_value / anchor.bot_total_value) - 1.0 if anchor.bot_total_value else 0.0
            sp = (r.spy_price / anchor.spy_price) - 1.0 if anchor.spy_price else 0.0
            bot_pct.append(bp)
            spy_pct.append(sp)
            excess_pct.append(bp - sp)
        return EquityCurve(
            timestamps=timestamps,
            bot_pct=bot_pct,
            spy_pct=spy_pct,
            excess_pct=excess_pct,
            anchor_tick_id=anchor.tick_id,
            anchor_bot_value=anchor.bot_total_value,
            anchor_spy_price=anchor.spy_price,
        )
    finally:
        s.close()
