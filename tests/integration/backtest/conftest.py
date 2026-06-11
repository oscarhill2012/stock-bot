"""Shared fixtures and LLM-stub helpers for backtest integration smoke tests.

Provides:
- ``_make_strategist_llm_response`` — synthetic StrategistLLMDecision payload.
- ``_make_per_ticker_analyst_llm_response`` — synthetic LlmTickerVerdict payload.
- ``smoke_result`` — module-scoped fixture that runs one tick of the
  ``baseline-2025-09`` window against a seeded SQLite cache, with all LLM
  agents short-circuited.  Returns a ``SmokeBundle`` namespace so per-concern
  test modules can access the ``RunResult``, the fixture cache path, and
  (lazily) the last-tick session state.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backtest.cache.store import CachedDataStore
from data.models import CompanyRatios, Filing, NewsArticle, OHLCBar


# ---------------------------------------------------------------------------
# Helpers — synthetic LLM response payloads
# ---------------------------------------------------------------------------

def _make_strategist_llm_response(tickers: list[str]):
    """Return a synthetic ``LlmResponse`` containing a valid ``StrategistLLMDecision``.

    The strategist's ``before_model_callback``, when it returns a non-None
    ``LlmResponse``, causes ADK to skip the real Gemini call and treat the
    returned content as the model's response.  ADK then validates the JSON
    text against ``output_schema=StrategistLLMDecision`` and writes it to state.

    Note: the payload also carries a ``target_weights`` field, which is a
    ``StrategistDecision``-level field not present on the narrow
    ``StrategistLLMDecision`` schema.  It is harmlessly ignored at ADK
    validation time (``StrategistLLMDecision`` has no ``extra="forbid"``), and
    the ``StrategistEnricher`` re-derives the real ``target_weights`` from the
    stances downstream — so the value embedded here has no effect on the run.

    Parameters
    ----------
    tickers:
        The watchlist tickers the decision should cover.

    Returns
    -------
    google.adk.models.LlmResponse
        A synthetic response with a ``StrategistLLMDecision`` JSON payload.
    """
    from google.adk.models import LlmResponse
    from google.genai import types as genai_types

    # Buy the first ticker with a small position so the smoke test can assert
    # that user:positions is non-empty after the run.  The remaining tickers
    # (if any) receive update stances (no trade, weight unchanged).
    #
    # iter-3: three-verb schema (buy / sell / update).  buy weight capped at
    # 0.05 per trade; no horizon / target_price / stop_price on TickerStance.
    first_ticker = tickers[0] if tickers else "AAPL"
    stances = []

    for t in tickers:
        if t == first_ticker:
            stances.append({
                "ticker":    t,
                "intent":    "buy",
                "weight":    0.04,
                "rationale": "Smoke test buy — exercising the full executor path.",
            })
        else:
            stances.append({
                "ticker":    t,
                "intent":    "update",
                "rationale": "Smoke test neutral stance — no real signal.",
            })

    target_weights = {t: (0.04 if t == first_ticker else 0.0) for t in tickers}

    decision = {
        "stances":        stances,
        "target_weights": target_weights,
        # ``new_positions`` removed in Band 6 — executor assembles PositionThesis
        # from the fill price + stance via apply_stance_to_thesis.
        "decision_tag":   "smoke_test_open",
        "reasoning":      "Smoke test run — opening one position to exercise executor.",
        "thesis":         "Smoke-test thesis: testing position persistence.",
        "confidence":     0.7,
    }

    return LlmResponse(
        content=genai_types.Content(
            parts=[genai_types.Part.from_text(text=json.dumps(decision))]
        )
    )


def _make_per_ticker_analyst_llm_response(agent_name: str):
    """Return a synthetic ``LlmResponse`` containing a valid ``LlmTickerVerdict``.

    Used for the per-ticker Fundamental and News ``LlmAgent`` analysts whose
    ``output_schema=LlmTickerVerdict`` (the narrow LLM emit-schema introduced
    by the 2026-05-25 schema split — see ``contract.evidence.LlmTickerVerdict``).
    The ticker is extracted from the agent name by stripping the well-known
    prefix (``"NewsAnalyst_"`` or ``"FundamentalAnalyst_"``).

    The payload omits ``rationale`` — that field was dropped from the LLM
    emit-schema (Vertex was padding it toward the cap), and
    ``LlmTickerVerdict`` declares ``extra="forbid"`` so including it would
    raise.  The downstream joiner inflates each emit into ``TickerVerdict``,
    on which ``rationale`` defaults to ``""``.

    Parameters
    ----------
    agent_name:
        The ADK agent name — e.g. ``"NewsAnalyst_AAPL"`` or
        ``"FundamentalAnalyst_MSFT"``.

    Returns
    -------
    google.adk.models.LlmResponse
        A synthetic response with an ``LlmTickerVerdict`` JSON payload for
        the single ticker extracted from ``agent_name``.
    """
    from google.adk.models import LlmResponse
    from google.genai import types as genai_types

    # Strip known prefixes to recover the ticker symbol.
    ticker = agent_name
    for prefix in ("NewsAnalyst_", "FundamentalAnalyst_"):
        if agent_name.startswith(prefix):
            ticker = agent_name[len(prefix):]
            break

    # ``report`` is required on every emit on ``LlmTickerVerdict`` (no
    # default, no Optional).  Two drivers is the minimum the
    # ``AnalystReport`` schema accepts.
    verdict = {
        "ticker":      ticker,
        "lean":        "neutral",
        "magnitude":   0.0,
        "confidence":  0.5,
        "is_no_data":  False,
        "key_factors": [],
        "report": {
            "summary": "Smoke-test stub report — verdict is neutral.",
            "drivers": [
                {
                    "name":      "stub_a",
                    "direction": "neutral",
                    "weight":    0.5,
                    "body":      "Smoke-test stub driver A.",
                },
                {
                    "name":      "stub_b",
                    "direction": "neutral",
                    "weight":    0.5,
                    "body":      "Smoke-test stub driver B.",
                },
            ],
        },
    }

    return LlmResponse(
        content=genai_types.Content(
            parts=[genai_types.Part.from_text(text=json.dumps(verdict))]
        )
    )


# ---------------------------------------------------------------------------
# Module-scope fixture: one-tick smoke run shared across all per-concern files
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def smoke_result(tmp_path_factory):
    """Run one tick of ``baseline-2025-09`` against a seeded fixture cache.

    Scope is ``"module"`` so the expensive ADK pipeline run executes exactly
    once per test module, not once per test function.  All four per-concern
    smoke-test files import this fixture and share a single run.

    Returns a ``SimpleNamespace`` (``SmokeBundle``) with the following
    attributes:

    - ``result`` — ``RunResult`` from ``Runner.run()``.
    - ``cache_path`` — ``Path`` to the SQLite fixture cache.
    - ``tickers`` — watchlist used for this run (``["AAPL"]``).
    - ``last_session_state`` — lazily-resolved property: reads the final
      tick's ADK session state from ``session.sqlite``.  Exposed as a plain
      ``dict`` so per-concern tests can introspect keys directly.

    Parameters
    ----------
    tmp_path_factory:
        Pytest's module-scoped temporary directory factory.
    """
    from backtest.runner import Runner
    from backtest.settings import BacktestSettings

    # ── Create a sandboxed temp directory for this module's run ──────────────
    tmp_path: Path = tmp_path_factory.mktemp("smoke_run")

    # ── Materialise the fixture cache ─────────────────────────────────────────
    # Per-window layout: ``<backtests_root>/<window>/store.sqlite``.
    cache_path = tmp_path / "backtests" / "baseline-2025-09" / "store.sqlite"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    store = CachedDataStore(cache_path)

    # Write 25 warm-up bars (2025-08-08 to 2025-09-01) plus the single window
    # day (2025-09-02) for AAPL.  RSI(14) and ATR(14) each need at least 15
    # bars; pct_change_20d needs 21.  The warm-up bars ensure those indicators
    # compute cleanly on the window day.
    _warm_start  = datetime.fromisoformat("2025-08-08T00:00:00+00:00")
    _window_days = [datetime.fromisoformat("2025-09-02T00:00:00+00:00")]
    _all_days    = [_warm_start + timedelta(days=i) for i in range(25)] + _window_days

    aapl_bars = [
        OHLCBar(
            timestamp = ts,
            open      = (145.0 + i * 0.10) - 0.5,
            high      = (145.0 + i * 0.10) + 1.0,
            low       = (145.0 + i * 0.10) - 1.0,
            close     =  145.0 + i * 0.10,   # gently trending so momentum is non-zero
            volume    =  1_000_000,
        )
        for i, ts in enumerate(_all_days)
    ]
    store.write_ohlcv("AAPL", aapl_bars)

    # Seed SPY benchmark bars.  SnapshotterAgent fetches ``SPY`` via the
    # registered price-history provider; during a backtest replay that routes
    # to the cache provider.  Without these rows the snapshotter raises on the
    # first tick (post-A-006 loud-fail).
    spy_bars = [
        OHLCBar(
            timestamp = ts,
            open      = (450.0 + i * 0.10) - 0.5,
            high      = (450.0 + i * 0.10) + 1.0,
            low       = (450.0 + i * 0.10) - 1.0,
            close     =  450.0 + i * 0.10,
            volume    =  5_000_000,
        )
        for i, ts in enumerate(_all_days)
    ]
    store.write_ohlcv("SPY", spy_bars)

    # Seed one news article and one filing so the cache-wrapper round-trip
    # (get_stock_news / get_company_filings) has non-empty results to return.
    # Both are dated before the 2025-09-02 window tick so they are visible
    # under point-in-time reads.
    store.write_news("AAPL", [
        NewsArticle(
            ticker       = "AAPL",
            headline     = "Apple holds steady ahead of September session",
            summary      = "Smoke-test fixture row.",
            url          = "https://example.invalid/aapl-fixture",
            source       = "fixture",
            published_at = datetime.fromisoformat("2025-08-29T15:00:00+00:00"),
        ),
    ])
    store.write_filings("AAPL", [
        Filing(
            ticker       = "AAPL",
            form_type    = "8-K",
            filed_at     = datetime.fromisoformat("2025-08-28T20:00:00+00:00"),
            accession_no = "0000320193-25-fixture",
            title        = "Fixture 8-K",
            url          = "https://example.invalid/aapl-8k",
            body_excerpt = "Smoke-test fixture filing.",
        ),
    ])
    store.write_company_ratios(
        "AAPL",
        CompanyRatios(
            ticker                 = "AAPL",
            long_name              = "Apple Inc.",
            sector                 = "Technology",
            market_cap             = 2_500_000_000_000,
            trailing_pe            = 28.0,
            forward_pe             = 26.0,
            beta                   = 1.2,
            dividend_yield         = 0.005,
            fifty_day_average      = 148.0,
            two_hundred_day_average= 145.0,
        ),
        as_of_date=datetime(2025, 8, 30).date(),
    )

    # ── Build settings and config paths ──────────────────────────────────────
    settings_obj = BacktestSettings(
        backtests_root               = str(tmp_path / "backtests"),
        ticks_per_day                = ["open"],   # one phase → one tick per session
        failed_tick_abort_ratio      = 1.0,        # never abort in smoke test
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )

    windows_path = tmp_path / "backtest_windows.json"
    windows_path.write_text(json.dumps({
        "baseline-2025-09": {
            "start": "2025-09-02",
            "end":   "2025-09-02",
            "notes": "Single-tick slice of the baseline window for smoke test.",
            "risk_free_rate_annual": 0.040,
        }
    }))

    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    tickers = ["AAPL"]

    # ── LLM patch helpers (defined inline so they close over ``tickers``) ────

    def _patched_build_strategist():
        """Build the strategist as ``SequentialAgent[ContextShim, mock LlmAgent, Enricher]``.

        Mirrors the live topology (minus ``RetryingAgentWrapper``) so the test
        exercises the production enrichment path.  The mock callback returns
        a synthetic ``StrategistLLMDecision`` before the real Gemini call fires.
        """
        from google.adk.agents import LlmAgent, SequentialAgent

        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.enricher import StrategistEnricher
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistLLMDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistLLMDecision without calling Gemini."""
            current_tickers = callback_context.state.get("tickers") or tickers
            return _make_strategist_llm_response(current_tickers)

        llm = LlmAgent(
            name                 = "Strategist",
            model                = "gemini-2.5-pro",
            instruction          = STRATEGIST_INSTRUCTION,
            output_schema        = StrategistLLMDecision,
            output_key           = "strategist_decision",
            before_model_callback= _mock_before,
        )

        return SequentialAgent(
            name       = "StrategistBranch",
            sub_agents = [StrategistContextShim(), llm, StrategistEnricher()],
        )

    def _patched_build_analyst_pool(tick_tickers: list[str]):
        """Build the analyst pool with all per-ticker LLM agents short-circuited.

        Phase 9 topology:
        ``SequentialAgent([ParallelAgent([Tech, Social]), Fund branch, News branch])``.

        The Fundamental and News branches each use a
        ``SequentialAgent[FetchAgent, ParallelAgent[IsolatedFailureWrapper×N], JoinerAgent]``
        topology where each per-ticker LLM node is named
        ``NewsAnalyst_<TICKER>`` or ``FundamentalAnalyst_<TICKER>``.

        Parameters
        ----------
        tick_tickers:
            Ticker list for the current tick, forwarded from ``build_pipeline``.
        """
        from google.adk.agents import LlmAgent, ParallelAgent, SequentialAgent

        from agents.analysts.fundamental.agent import build_fundamental_branch
        from agents.analysts.heuristics import load_heuristics
        from agents.analysts.news.agent import build_news_branch
        from agents.analysts.social.agent import _build_social_analyst
        from agents.analysts.technical.agent import _build_technical_analyst

        h = load_heuristics()

        # Deterministic analysts are BaseAgent subclasses — no LLM involved.
        technical = _build_technical_analyst(h.technical)
        social    = _build_social_analyst(h.social)

        # Build per-ticker fan-out branches for this tick's tickers.
        fundamental_branch = build_fundamental_branch(
            h.fundamental_vocabulary, tickers=tick_tickers
        )
        news_branch = build_news_branch(h.news_vocabulary, tickers=tick_tickers)

        def _mock_analyst_before(callback_context, llm_request):
            """Return a synthetic TickerVerdict without calling Gemini.

            ADK sets ``callback_context.agent_name`` to the currently-running
            agent's name, which encodes the ticker.
            """
            agent_name = getattr(callback_context, "agent_name", "") or ""
            return _make_per_ticker_analyst_llm_response(agent_name)

        def _install_mock_on_branch(branch):
            """Walk ``branch.sub_agents`` and mock every per-ticker LlmAgent.

            Recurses through ``ParallelAgent`` containers and the ``.inner``
            wrapper chain (``IsolatedFailureWrapper`` → ``RetryingAgentWrapper``
            → ``LlmAgent``) to find every leaf ``LlmAgent``.
            """
            for sub in getattr(branch, "sub_agents", []):
                # Recurse into nested container agents before inspecting leaves.
                if getattr(sub, "sub_agents", None):
                    _install_mock_on_branch(sub)
                    continue

                # Hop through wrapper layers until we reach an LlmAgent.
                node = sub
                while node is not None and not isinstance(node, LlmAgent):
                    node = getattr(node, "inner", None)

                if isinstance(node, LlmAgent) and node.name.startswith(
                    ("NewsAnalyst_", "FundamentalAnalyst_")
                ):
                    node.before_model_callback = _mock_analyst_before

        _install_mock_on_branch(fundamental_branch)
        _install_mock_on_branch(news_branch)

        parallel_deterministic = ParallelAgent(
            name       = "DeterministicAnalysts",
            sub_agents = [technical, social],
        )

        return SequentialAgent(
            name       = "AnalystPool",
            sub_agents = [parallel_deterministic, fundamental_branch, news_branch],
        )

    # ── Execute the patched run ───────────────────────────────────────────────
    with (
        patch(
            "orchestrator.pipeline._build_strategist",
            side_effect=_patched_build_strategist,
        ),
        patch(
            "orchestrator.pipeline._build_analyst_pool",
            side_effect=_patched_build_analyst_pool,
        ),
    ):
        runner = Runner(
            settings       = settings_obj,
            windows_path   = windows_path,
            watchlist_path = watchlist_path,
        )
        # tick_limit=1 caps the run at exactly one tick, keeping LLM usage
        # minimal regardless of what ``generate_ticks`` returns.
        result = runner.run("baseline-2025-09", tick_limit=1)

    # ── Build a lazy bundle so expensive session reads only happen when needed ─
    # Cache the session state on first access so multiple test files can call
    # ``.last_session_state`` without redundant SQLite round-trips.
    _state_cache: dict[str, object] = {}

    def _get_last_session_state() -> dict:
        """Read and cache the final-tick ADK session state from session.sqlite.

        Lazily resolved on first call.  Raises ``AssertionError`` if the
        session sqlite is missing or contains no sessions — indicating a broken
        run, not a state-shape violation.

        Returns
        -------
        dict
            The ``session.state`` dict from the last tick's ADK session.
        """
        if "state" not in _state_cache:
            from google.adk.sessions import DatabaseSessionService as _DSS

            session_sqlite = result.run_dir / "session.sqlite"
            assert session_sqlite.exists(), (
                f"Smoke run did not create session sqlite at {session_sqlite}; "
                "DatabaseSessionService wiring is broken or run_dir is wrong."
            )

            svc      = _DSS(db_url=f"sqlite+aiosqlite:///{session_sqlite}")
            app_name = "StockBot-backtest-baseline-2025-09"

            sessions = asyncio.run(
                svc.list_sessions(app_name=app_name, user_id="stockbot")
            )
            assert sessions and sessions.sessions, (
                "DatabaseSessionService.list_sessions returned no sessions after "
                f"smoke run; app_name={app_name!r}, user_id='stockbot'."
            )

            last_sid = sessions.sessions[-1].id
            last_session = asyncio.run(
                svc.get_session(
                    app_name=app_name, user_id="stockbot", session_id=last_sid
                )
            )
            assert last_session is not None, "last tick session must be fetchable"

            # Store as plain dict for safe cross-test reads.
            _state_cache["state"] = dict(last_session.state)

        return _state_cache["state"]   # type: ignore[return-value]

    return SimpleNamespace(
        result               = result,
        cache_path           = cache_path,
        tickers              = tickers,
        get_last_session_state = _get_last_session_state,
    )
