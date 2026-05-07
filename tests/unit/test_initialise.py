# tests/unit/test_initialise.py
"""initialise: pre-flight checks, anchor snapshot, scheduler resume."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lifecycle.initialise import initialise, InitResult, NonEmptyTablesError, EnvVarMissingError, BrokerCashMismatch
from orchestrator.persistence import (
    PortfolioSnapshotRow,
    create_all,
    make_engine,
    make_session_factory,
    save_portfolio_snapshot,
)


class _StubBroker:
    def __init__(self, cash):
        self._cash = cash

    async def get_portfolio(self):
        from broker.portfolio import Portfolio
        return Portfolio(cash=self._cash, positions={})


@pytest.mark.asyncio
async def test_initialise_writes_anchor_on_empty_db(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    db_url = f"sqlite:///{db_path}"
    create_all(make_engine(db_url))

    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")

    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "resume_job", lambda name: None)

    # Stub yfinance SPY price
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    res = await initialise(
        db_url=db_url,
        starting_capital=10000.0,
        broker_mode="paper",
        watchlist=["AAPL"],
        broker=_StubBroker(10000.0),
        scheduler_job=None,
    )
    assert isinstance(res, InitResult)
    # Anchor row exists
    S = make_session_factory(make_engine(db_url))
    s = S()
    rows = s.query(PortfolioSnapshotRow).all()
    assert len(rows) == 1
    anchor = rows[0]
    assert anchor.tick_id == "init"
    assert anchor.bot_total_value == 10000.0
    assert anchor.spy_price == 480.0
    s.close()


@pytest.mark.asyncio
async def test_refuses_on_non_empty_tables(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    db_url = f"sqlite:///{db_path}"
    create_all(make_engine(db_url))
    S = make_session_factory(make_engine(db_url))
    s = S()
    save_portfolio_snapshot(s, {
        "tick_id": "old", "recorded_at": datetime.now(tz=timezone.utc),
        "bot_total_value": 1.0, "bot_cash": 1.0,
        "bot_positions_value": 0.0, "bot_position_count": 0,
        "spy_price": 1.0, "spy_value_if_held": 1.0,
        "bot_return_pct": 0.0, "spy_return_pct": 0.0, "excess_return_pct": 0.0,
        "holdings_breakdown": {},
    })
    s.commit()
    s.close()

    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(NonEmptyTablesError):
        await initialise(
            db_url=db_url,
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(10000.0),
            scheduler_job=None,
        )


@pytest.mark.asyncio
async def test_refuses_on_missing_env_var(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    create_all(make_engine(f"sqlite:///{db_path}"))
    monkeypatch.delenv("TRADING212_API_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(EnvVarMissingError):
        await initialise(
            db_url=f"sqlite:///{db_path}",
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(10000.0),
            scheduler_job=None,
        )


@pytest.mark.asyncio
async def test_refuses_on_broker_cash_mismatch(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    create_all(make_engine(f"sqlite:///{db_path}"))
    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)

    with pytest.raises(BrokerCashMismatch):
        await initialise(
            db_url=f"sqlite:///{db_path}",
            starting_capital=10000.0,
            broker_mode="paper",
            watchlist=["AAPL"],
            broker=_StubBroker(9500.0),  # mismatch
            scheduler_job=None,
        )
