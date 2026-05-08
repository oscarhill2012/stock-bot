# tests/unit/test_initialise_cli.py
"""initialise CLI: argv parsing, broker construction, calls library."""
from __future__ import annotations

import pytest

from scripts import initialise as cli


@pytest.mark.asyncio
async def test_main_calls_initialise(tmp_path, monkeypatch):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setenv("STOCKBOT_ENV", "dev")
    monkeypatch.setenv("TRADING212_API_KEY", "x")
    monkeypatch.setenv("FINNHUB_API_KEY", "x")

    from orchestrator.persistence import create_all, make_engine
    create_all(make_engine(f"sqlite:///{db_path}"))

    # Stub broker construction & SPY fetch & scheduler
    class _Stub:
        async def get_portfolio(self):
            from broker.portfolio import Portfolio
            return Portfolio(cash=10000.0, positions={})

    monkeypatch.setattr("scripts.initialise._build_broker", lambda mode: _Stub())
    monkeypatch.setattr("lifecycle.initialise._fetch_spy_price", lambda: 480.0)
    from lifecycle import scheduler
    monkeypatch.setattr(scheduler, "resume_job", lambda name: None)

    rc = await cli.main_async([
        "--db-url", f"sqlite:///{db_path}",
        "--capital", "10000",
        "--broker-mode", "paper",
        "--watchlist", "config/watchlist.json",
    ])
    assert rc == 0
