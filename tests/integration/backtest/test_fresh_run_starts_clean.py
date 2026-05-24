# tests/integration/backtest/test_fresh_run_starts_clean.py
"""Regression guard: ``--fresh`` deletes ``runs/<run-id>/session.sqlite``
before the run begins so a re-run of the same window starts from an empty
``user_state`` row.

Without this guarantee, prior-run thesis leaks into tick 1 of the re-run —
the exact failure mode Spec B (foundational-thesis-memory.md) was written to
prevent.

Design note
-----------
This test exercises the ``--fresh`` plumbing via ``Runner.run()`` (the public
API), but the full LLM pipeline is NOT invoked — we verify the SQLite file
is deleted and recreated (empty) when ``fresh=True``, not that a particular
decision was made.  The "positions are empty at tick 1" assertion is covered
at the integration level by the end-to-end smoke test which exercises the full
pipeline.

Task 2.6 acceptance criterion: ``--fresh`` deletes
``runs/<run-id>/session.sqlite``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backtest.cache.store import CachedDataStore
from data.models import OHLCBar


@pytest.fixture
def minimal_fixture_cache(tmp_path: Path) -> Path:
    """Write a one-bar AAPL cache so Runner's pre-flight doesn't skip everything.

    Placed at ``<tmp_path>/backtests/baseline-2025-09/store.sqlite`` so the
    per-window layout matches the smoke test and the runner resolves the cache
    path automatically from the ``BacktestSettings``.
    """
    cache_path = tmp_path / "backtests" / "baseline-2025-09" / "store.sqlite"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    store = CachedDataStore(cache_path)

    # One bar is enough for the pre-flight check to not skip AAPL.
    store.write_ohlcv("AAPL", [
        OHLCBar(
            timestamp=datetime(2025, 9, 2, tzinfo=UTC),
            open=150.0,
            high=152.0,
            low=149.0,
            close=151.0,
            volume=1_000_000,
        ),
    ])
    return cache_path


def _plant_session_sqlite(run_dir: Path) -> Path:
    """Create a dummy session.sqlite in ``run_dir`` to simulate a prior run.

    Writes a minimal SQLite database with a fake ``sessions`` table and one
    row so the ``--fresh`` plumbing has something to delete.

    Returns
    -------
    Path
        The path to the created file.
    """
    session_path = run_dir / "session.sqlite"
    session_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(session_path))
    conn.execute(
        "CREATE TABLE sessions "
        "(app_name TEXT, user_id TEXT, id TEXT PRIMARY KEY, state TEXT)"
    )
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?)",
        ("StockBot-backtest-baseline-2025-09", "stockbot", "prior-run-session",
         '{"user:positions": {"AVGO": {"ticker": "AVGO"}}, "user:thesis": "AVGO is bullish"}'),
    )
    conn.commit()
    conn.close()

    return session_path


@pytest.mark.slow
def test_fresh_deletes_session_sqlite(
    tmp_path: Path,
    minimal_fixture_cache: Path,
) -> None:
    """``Runner.run(…, fresh=True)`` must delete ``session.sqlite`` before
    the run starts so the new run begins with an empty user_state row.

    Approach: plant a fake ``session.sqlite`` that contains prior-run state
    (AVGO position), then run the same window with ``fresh=True``.  Assert
    that the original file was deleted — confirmed by the absence of the
    planted rows in the recreated file.
    """
    import asyncio
    from unittest.mock import MagicMock, patch

    from backtest.runner import Runner
    from backtest.settings import BacktestSettings

    # Single-session window for minimum tick count (one tick → one phase).
    run_id = "baseline-2025-09-fresh-test"

    settings_obj = BacktestSettings(
        backtests_root               = str(tmp_path / "backtests"),
        ticks_per_day                = ["open"],
        failed_tick_abort_ratio      = 1.0,
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )

    windows_path = tmp_path / "backtest_windows.json"
    windows_path.write_text(json.dumps({
        "baseline-2025-09": {
            "start": "2025-09-02",
            "end":   "2025-09-02",
            "notes": "Single-tick slice for fresh-flag test.",
        }
    }))

    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    # Construct the expected run directory so we can plant the session file.
    from backtest.settings import runs_root_for_window
    run_dir = runs_root_for_window(settings_obj, "baseline-2025-09") / run_id

    # Prepare Runner instance before planting so the mkdir in Runner.__init__
    # doesn't race with our manual setup.
    runner = Runner(
        settings=settings_obj,
        windows_path=windows_path,
        watchlist_path=watchlist_path,
    )

    # Plant the prior-run session file with stale position data.
    run_dir.mkdir(parents=True, exist_ok=True)
    session_path = _plant_session_sqlite(run_dir)

    assert session_path.exists(), "Failed to plant session.sqlite for test setup"

    # Read the stale row to verify it exists before the fresh run.
    conn = sqlite3.connect(str(session_path))
    rows = conn.execute("SELECT * FROM sessions").fetchall()
    conn.close()
    assert len(rows) == 1, "Expected one planted session row before fresh run"

    # Mock the full pipeline so no LLM / network calls are made.
    # We only need the --fresh plumbing to fire, not the tick to complete.
    tickers = ["AAPL"]

    def _patched_build_strategist():
        from google.adk.agents import LlmAgent, SequentialAgent
        from agents.strategist.agent import _strategist_validation_callback
        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistDecision
        from google.adk.models import LlmResponse
        from google.genai import types as genai_types

        stances = [{"ticker": t, "intent": "hold", "reason": "fresh-test stub"} for t in tickers]
        decision = {
            "stances": stances, "target_weights": {t: 0.0 for t in tickers},
            "decision_tag": "fresh_test_hold", "reasoning": "stub",
            "thesis": "stub", "confidence": 0.5,
        }

        def _mock_before(ctx, req):
            return LlmResponse(content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=json.dumps(decision))]
            ))

        llm = LlmAgent(
            name="Strategist", model="gemini-2.5-pro",
            instruction=STRATEGIST_INSTRUCTION, output_schema=StrategistDecision,
            output_key="strategist_decision",
            after_agent_callback=_strategist_validation_callback,
            before_model_callback=_mock_before,
        )
        return SequentialAgent(name="StrategistBranch", sub_agents=[StrategistContextShim(), llm])

    def _patched_build_analyst_pool(tick_tickers):
        from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent
        from agents.analysts.fundamental.agent import build_fundamental_branch
        from agents.analysts.heuristics import load_heuristics
        from agents.analysts.news.agent import build_news_branch
        from agents.analysts.social.agent import _build_social_analyst
        from agents.analysts.technical.agent import _build_technical_analyst
        from google.adk.models import LlmResponse
        from google.genai import types as genai_types
        import json as _json

        h = load_heuristics()
        technical = _build_technical_analyst(h.technical)
        social    = _build_social_analyst(h.social)
        fundamental_branch = build_fundamental_branch(h.fundamental_vocabulary, tickers=tick_tickers)
        news_branch = build_news_branch(h.news_vocabulary, tickers=tick_tickers)

        def _mock_before(ctx, req):
            agent_name = getattr(ctx, "agent_name", "") or ""
            ticker = agent_name
            for prefix in ("NewsAnalyst_", "FundamentalAnalyst_"):
                if agent_name.startswith(prefix):
                    ticker = agent_name[len(prefix):]
                    break
            verdict = {
                "ticker": ticker, "lean": "neutral", "magnitude": 0.0,
                "confidence": 0.5, "rationale": "stub", "key_factors": [],
                "is_no_data": False,
                "report": {"summary": "stub", "drivers": [
                    {"name": "a", "direction": "neutral", "weight": 0.5, "body": "stub"},
                    {"name": "b", "direction": "neutral", "weight": 0.5, "body": "stub"},
                ]},
            }
            return LlmResponse(content=genai_types.Content(
                parts=[genai_types.Part.from_text(text=_json.dumps(verdict))]
            ))

        def _install_mocks(branch):
            for sub in getattr(branch, "sub_agents", []):
                if getattr(sub, "sub_agents", None):
                    _install_mocks(sub)
                    continue
                node = sub
                while node is not None and not isinstance(node, LlmAgent):
                    node = getattr(node, "inner", None)
                if isinstance(node, LlmAgent) and node.name.startswith(
                    ("NewsAnalyst_", "FundamentalAnalyst_")
                ):
                    node.before_model_callback = _mock_before

        _install_mocks(fundamental_branch)
        _install_mocks(news_branch)
        return SequentialAgent(name="AnalystPool", sub_agents=[
            ParallelAgent(name="DeterministicAnalysts", sub_agents=[technical, social]),
            fundamental_branch,
            news_branch,
        ])

    mock_yf_ticker = MagicMock()
    mock_yf_ticker.history.return_value = MagicMock(
        empty=False,
        __getitem__=lambda self, key: MagicMock(
            iloc=MagicMock(__getitem__=lambda self2, idx: 450.0)
        ),
    )

    with (
        patch("orchestrator.pipeline._build_strategist", side_effect=_patched_build_strategist),
        patch("orchestrator.pipeline._build_analyst_pool", side_effect=_patched_build_analyst_pool),
        patch("yfinance.Ticker", return_value=mock_yf_ticker),
    ):
        result = runner.run(
            "baseline-2025-09",
            tick_limit=1,
            run_id_override=run_id,
            fresh=True,
        )

    # Primary assertion: the planted session row should not appear in the
    # new session file.  A ``--fresh`` run deletes the old file before
    # creating a new one, so the prior-run position data is gone.
    assert session_path.exists(), "session.sqlite should be recreated by the run"

    conn = sqlite3.connect(str(session_path))
    old_rows = conn.execute(
        "SELECT * FROM sessions WHERE id = 'prior-run-session'"
    ).fetchall()
    conn.close()

    assert not old_rows, (
        "Prior-run session row survived --fresh! "
        "The --fresh cleanup did not delete the old session.sqlite."
    )

    assert result.status in {"completed", "completed_with_failures"}, (
        f"Run status unexpected: {result.status!r}"
    )
