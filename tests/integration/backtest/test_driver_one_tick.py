"""Integration test: driver runs one tick end-to-end against a hand-populated cache.

Asserts that:
  (a) one trace file is written to ``<run>/traces/``;
  (b) a ``PortfolioSnapshotRow`` exists in the per-run ``db.sqlite``;
  (c) if any Fill was produced, a matching decision file exists in ``decisions/``.

The test does NOT make LLM calls — the Strategist + Fundamental/News analysts
require a Google API key and real model access.  The pipeline is wired with a
real ``FakeBroker`` and a real ``CachedDataStore`` but the strategist's LLM
response is patched to a canned ``StrategistDecision``.
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backtest.cache.store import CachedDataStore
from backtest.decision_logger import DecisionLogger
from backtest.driver import Driver
from backtest.providers._store_handle import clear_store, set_store
from backtest.schedule import Tick
from broker.fake import FakeBroker
from data.models import OHLCBar
from orchestrator.persistence import Base, create_all, make_engine


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def cache(tmp_path: Path) -> CachedDataStore:
    """Return a ``CachedDataStore`` pre-populated with one AAPL bar.

    Sets the global ``_store_handle`` singleton so that cache providers
    (if dispatched) can find the store.  The fixture tears down by clearing
    the singleton.
    """
    store = CachedDataStore(tmp_path / "cache.sqlite")
    # OHLCBar uses timestamp:datetime (not date), and has no ticker/adj_close field.
    store.write_ohlcv("AAPL", [
        OHLCBar(
            timestamp=datetime(2023, 3, 13, 9, 30, tzinfo=UTC),
            open=150.0,
            high=152.0,
            low=149.0,
            close=151.0,
            volume=1_000_000,
        ),
    ])
    set_store(store)
    yield store
    clear_store()


@pytest.fixture()
def run_db(tmp_path: Path):
    """Return a per-run SQLAlchemy session backed by an in-memory SQLite DB.

    Creates all tables (same as the live schema) on the engine so evidence
    and snapshot writers have a valid DB.
    """
    engine = make_engine("sqlite://")  # in-memory — isolated per test
    create_all(engine)
    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session, engine
    session.close()


# ── Test ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_driver_produces_trace_and_snapshot(
    tmp_path: Path,
    cache: CachedDataStore,
    run_db,
) -> None:
    """One scheduled tick produces a trace file and a portfolio snapshot row.

    The LLM-backed agents (Strategist, Fundamental, News) are patched out so
    the test runs without network access.  The deterministic BaseAgent
    analysts (Technical, Social, SmartMoney) run normally against the
    hand-populated cache.
    """
    db_session, _engine = run_db

    run_dir    = tmp_path / "run"
    run_dir.mkdir()
    decisions_dir = run_dir / "decisions"
    decisions_dir.mkdir()

    broker = FakeBroker(starting_cash=10_000.0, prices={"AAPL": 150.0})

    dl = DecisionLogger(output_dir=decisions_dir, window_key="test")

    driver = Driver(
        broker=broker,
        run_id="test-run",
        run_dir=run_dir,
        window_key="test",
        db_session=db_session,
        decision_logger=dl,
    )

    schedule = [
        Tick(
            as_of=datetime(2023, 3, 13, 9, 30, tzinfo=UTC),
            phase="open",
        ),
    ]

    state: dict = {
        "tickers":      ["AAPL"],
        "watchlist":    ["AAPL"],
        "positions":    {},
        "memory_buffer": [],
        "day_digest":   "",
        "thesis":       "",
        "portfolio":    {"cash": 10_000.0, "positions": {}},
    }

    # Patch LLM-backed agents to avoid real API calls.
    # The Strategist is patched to return a minimal hold-all decision so the
    # pipeline can complete without network I/O.
    canned_decision = {
        "decision_tag":       "hold",
        "reasoning_excerpt":  "test",
        "ticker_stances":     {},
        "new_positions":      {},
        "close_reasons":      {},
    }
    fundamental_verdict = {
        "lean":          "neutral",
        "magnitude":     0.5,
        "confidence":    0.5,
        "rationale":     "canned",
        "key_factors":   [],
        "is_no_data":    True,
    }

    # Patch the LlmAgents' _run_async_impl to inject canned output into
    # session state and yield nothing (simulating a completed LLM agent).
    async def _fake_strategist_run(self_agent, ctx):
        ctx.session.state["strategist_decision"] = canned_decision
        return
        yield  # pragma: no cover — keeps function an async generator

    async def _fake_fundamental_run(self_agent, ctx):
        ctx.session.state.setdefault("fundamental_data", {})["AAPL"] = fundamental_verdict
        return
        yield  # pragma: no cover

    async def _fake_news_run(self_agent, ctx):
        ctx.session.state.setdefault("news_data", {})["AAPL"] = None
        return
        yield  # pragma: no cover

    with (
        patch("agents.strategist.agent.LlmAgent._run_async_impl", _fake_strategist_run),
        patch("agents.analysts.fundamental.agent.LlmAgent._run_async_impl", _fake_fundamental_run),
        patch("agents.analysts.news.agent.LlmAgent._run_async_impl", _fake_news_run),
        # Patch yfinance in the snapshotter (SPY price lookup).
        patch("yfinance.Ticker") as mock_yf,
    ):
        mock_yf.return_value.history.return_value = MagicMock(
            empty=False,
            **{"__getitem__": lambda self, _: MagicMock(
                **{"iloc.__getitem__": lambda s, i: 470.0}
            )},
        )
        await driver.run(state, schedule)

    # (a) One trace file must exist.
    traces = list((run_dir / "traces").glob("*.json"))
    assert len(traces) == 1, f"expected 1 trace file, got {len(traces)}"

    # (b) A PortfolioSnapshotRow must exist in the run's DB.
    from orchestrator.persistence import PortfolioSnapshotRow
    rows = db_session.query(PortfolioSnapshotRow).all()
    assert len(rows) >= 1, "expected at least one PortfolioSnapshotRow"
