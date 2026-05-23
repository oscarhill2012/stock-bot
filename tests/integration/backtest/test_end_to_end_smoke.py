"""Smoke test: full Runner over a single tick against a fixture cache.

Marked ``@pytest.mark.slow`` so it is excluded from the default ``pytest``
run (which uses ``--strict-markers`` + no ``-m slow`` flag).  To run it
explicitly::

    PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow

The test exercises the entire stack end-to-end:

- Cache providers (OHLCV + CompanyRatios).
- ``as_of`` point-in-time migration through the live data layer.
- Analyst fetch callbacks (technical, fundamental, news, social, smart_money).
- The full ADK pipeline (AnalystPool → EvidenceWriter → Strategist →
  StrategistDecisionWriter → RiskGate → Executor → MemoryWriter → Snapshotter).
- FakeBroker portfolio tracking.
- DecisionLogger snapshot files.
- End-of-run reporting (equity_curve.png + metrics.md).

LLM mocking (Phase 9 update)
-----------------------------
The strategist (Gemini 2.5 Pro) and the per-ticker LLM analyst agents
(``NewsAnalyst_<TICKER>`` / ``FundamentalAnalyst_<TICKER>``) are mocked via a
``before_model_callback`` shim that returns a synthetic ``LlmResponse`` before
the real model is ever called.

Phase 9 replaced the batched ``NewsAnalyst`` / ``FundamentalAnalyst`` LlmAgents
(one call → ``VerdictBatch``) with a per-ticker fan-out:
  ``SequentialAgent[FetchAgent, *IsolatedFailureWrapper(RetryingAgentWrapper(LlmAgent)), JoinerAgent]``

Each per-ticker ``LlmAgent`` is named ``NewsAnalyst_<TICKER>`` or
``FundamentalAnalyst_<TICKER>`` and expects a ``TickerVerdict`` (single-ticker
JSON), not a ``VerdictBatch``.  The mock helper
``_make_per_ticker_analyst_llm_response`` extracts the ticker from the agent
name and emits the matching single-verdict payload.

The factory patch installs the mock callback by walking each branch's
``sub_agents`` and setting ``before_model_callback`` on every ``LlmAgent``
whose name starts with ``"NewsAnalyst_"`` or ``"FundamentalAnalyst_"``.

yfinance
--------
``SnapshotterAgent`` calls ``yfinance.Ticker("SPY").history(period="1d")`` for
the SPY benchmark price.  That is patched so no network request leaves the
test process.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backtest.cache.store import CachedDataStore
from data.models import CompanyRatios, Filing, NewsArticle, OHLCBar
from datetime import timedelta


# ---------------------------------------------------------------------------
# Helpers — synthetic LLM response payloads
# ---------------------------------------------------------------------------

def _make_strategist_llm_response(tickers: list[str]):
    """Return a synthetic ``LlmResponse`` containing a valid ``StrategistDecision``.

    The strategist's ``before_model_callback``, when it returns a non-None
    ``LlmResponse``, causes ADK to skip the real Gemini call and treat the
    returned content as the model's response.  ADK then validates the JSON
    text against ``output_schema=StrategistDecision`` and writes it to state.

    Parameters
    ----------
    tickers:
        The watchlist tickers the decision should cover.

    Returns
    -------
    google.adk.models.LlmResponse
        A synthetic response with a ``StrategistDecision`` JSON payload.
    """
    from google.adk.models import LlmResponse
    from google.genai import types as genai_types

    # Open the first ticker with a small position so the smoke test can assert
    # that user:positions is non-empty after the run.  The remaining tickers
    # (if any) receive neutral (hold) stances.
    #
    # Note: ``preferred_weight > 0`` is required to trigger an "open" lifecycle
    # action in ``derive_lifecycle_action`` (current=0, preferred>0 → "open").
    # ``new_positions`` is a DERIVED field — omit it from the mock payload and
    # let ``_strategist_validation_callback`` compute it from the stances via
    # ``derive_legacy_fields``.  Including it pre-populated would cause a
    # datetime-serialisation error because Pydantic's ``model_validate`` converts
    # the ISO strings to ``datetime`` objects, and the derived overwrite path
    # (``decision.new_positions = derived.new_positions``) would not fire for
    # ``preferred_weight=0.0`` stances.
    first_ticker = tickers[0] if tickers else "AAPL"
    stances = []
    for t in tickers:
        if t == first_ticker:
            # ``preferred_weight=0.10`` (non-zero) is the trigger for
            # ``derive_lifecycle_action`` to return "open" (current=0 → new>0).
            stances.append({
                "ticker":           t,
                "preferred_weight": 0.10,
                "conviction":       0.7,
                "rationale":        "Smoke test open — exercising the full executor path.",
                "intent":           "open",
                "weight":           0.10,
                "horizon":          "swing",
                "target_price":     170.0,
                "stop_price":       140.0,
                "catalyst":         "Smoke-test trigger",
            })
        else:
            stances.append({
                "ticker":          t,
                "preferred_weight": 0.0,
                "conviction":       0.5,
                "rationale":        "Smoke test neutral stance — no real signal.",
            })

    target_weights = {t: (0.10 if t == first_ticker else 0.0) for t in tickers}

    decision = {
        "stances":        stances,
        "target_weights": target_weights,
        # ``new_positions`` intentionally omitted — derived by the strategist
        # after-callback from the stances; do not pre-populate to avoid
        # datetime-serialisation issues when Pydantic parses the JSON payload.
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
    """Return a synthetic ``LlmResponse`` containing a valid ``TickerVerdict``.

    Used for the per-ticker Fundamental and News ``LlmAgent`` analysts
    (Phase 9), whose ``output_schema=TickerVerdict``.  The ticker is
    extracted from the agent name by stripping the well-known prefix
    (``"NewsAnalyst_"`` or ``"FundamentalAnalyst_"``).

    Parameters
    ----------
    agent_name:
        The ADK agent name — e.g. ``"NewsAnalyst_AAPL"`` or
        ``"FundamentalAnalyst_MSFT"``.

    Returns
    -------
    google.adk.models.LlmResponse
        A synthetic response with a ``TickerVerdict`` JSON payload for the
        single ticker extracted from ``agent_name``.
    """
    from google.adk.models import LlmResponse
    from google.genai import types as genai_types

    # Strip known prefixes to recover the ticker symbol.
    ticker = agent_name
    for prefix in ("NewsAnalyst_", "FundamentalAnalyst_"):
        if agent_name.startswith(prefix):
            ticker = agent_name[len(prefix):]
            break

    # ``report`` is schema-required whenever ``is_no_data=False`` (the
    # contract on ``AnalystVerdict._report_required_when_data_present``).
    # Two drivers is the minimum the AnalystReport schema accepts.
    verdict = {
        "ticker":      ticker,
        "lean":        "neutral",
        "magnitude":   0.0,
        "confidence":  0.5,
        "rationale":   "Smoke test stub.",
        "key_factors": [],
        "is_no_data":  False,
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
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_cache(tmp_path: Path) -> Path:
    """Materialise a 1-tick OHLCV + CompanyRatios cache for AAPL.

    The cache is written to ``tmp_path/backtests/baseline-2025-09/store.sqlite``
    and the path to that file is returned for injection into the Runner
    settings.

    The single window day chosen (2025-09-02) is the first NYSE session of
    the ``baseline-2025-09`` window declared in ``config/backtest_windows.json``
    — keeps the smoke test aligned with the canonical baseline window while
    only exercising one tick to minimise LLM usage when the test runs.

    Parameters
    ----------
    tmp_path:
        Pytest's temporary directory for this test invocation.

    Returns
    -------
    Path
        Absolute path to the SQLite cache file.
    """
    # Per-window: place the fixture cache where the runner will look for the
    # ``baseline-2025-09`` window (``<backtests_root>/<window>/store.sqlite``).
    cache_path = tmp_path / "backtests" / "baseline-2025-09" / "store.sqlite"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    store = CachedDataStore(cache_path)

    # Write 25 warm-up bars (2025-08-08 to 2025-09-01) plus the single
    # window day (2025-09-02) for AAPL.
    #
    # RSI(14) and ATR(14) each need at least 15 bars; pct_change_20d needs
    # 21.  With only the 1 window bar the technical extractor's no-data
    # guard fires and is_no_data=True.  The warm-up bars give the extractor
    # enough history to compute at least rsi_14 and atr_pct_14, ensuring the
    # Phase 7 is_no_data assertion passes on this fixture cache.
    #
    # Close prices step up by 0.10 per bar so pct_change_5d is non-zero.
    _aapl_bars = []
    _warm_start = datetime.fromisoformat("2025-08-08T00:00:00+00:00")
    _window_days = [
        datetime.fromisoformat("2025-09-02T00:00:00+00:00"),
    ]
    _all_days = [_warm_start + timedelta(days=i) for i in range(25)] + _window_days
    for i, ts in enumerate(_all_days):
        _close = 145.0 + i * 0.10   # gently trending so momentum features are non-zero
        _aapl_bars.append(OHLCBar(
            timestamp=ts,
            open=_close - 0.5,
            high=_close + 1.0,
            low=_close - 1.0,
            close=_close,
            volume=1_000_000,
        ))
    store.write_ohlcv("AAPL", _aapl_bars)

    # Write one CompanyRatios snapshot so the fundamental cache provider
    # can satisfy point-in-time reads during the backtest window.
    # Seed one news article + one filing so the cache wrappers
    # (get_stock_news / get_company_filings) have something to return.
    # Without these rows, a silent TypeError inside the wrapper — like the
    # Phase 7.5 regression where lookback_days was not forwarded — would go
    # undetected because empty bundles are equivalent to "no rows in store".
    # Both rows are dated before the 2025-09-02 tick so they are visible at
    # point-in-time when the analyst fetches kick off.
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
            ticker="AAPL",
            long_name="Apple Inc.",
            sector="Technology",
            market_cap=2_500_000_000_000,
            trailing_pe=28.0,
            forward_pe=26.0,
            beta=1.2,
            dividend_yield=0.005,
            fifty_day_average=148.0,
            two_hundred_day_average=145.0,
        ),
        as_of_date=datetime(2025, 8, 30).date(),
    )

    return cache_path


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_end_to_end_run_produces_full_artefact_tree(
    tmp_path: Path,
    fixture_cache: Path,
) -> None:
    """One Runner.run() over a single tick produces the expected artefact tree.

    Asserts:
    - ``manifest.json`` exists and reports a terminal status.
    - ``traces/`` directory exists (one ``.json`` trace file per tick).
    - ``report/metrics.md`` exists with non-NaN metric values.
    - ``report/equity_curve.png`` exists.

    The window is the first session of ``baseline-2025-09`` with
    ``ticks_per_day=["open"]`` so exactly one tick fires — matches the
    project-wide rule that backtest tests run one tick on the baseline
    window to keep LLM usage minimal.

    LLM agents (Strategist, Fundamental, News) are short-circuited via a
    synthetic ``before_model_callback`` so no Gemini calls are made.
    yfinance (SPY lookup in Snapshotter) is monkeypatched to return a flat
    450.0 price without hitting the network.
    """
    from backtest.runner import Runner

    # ── Write settings + windows + watchlist under tmp_path ──────────────────
    # Build a typed BacktestSettings directly — Phase 7.5 dropped the
    # tz/open_time/close_time keys (session times now come from
    # pandas_market_calendars) and switched Runner to a `settings=` kwarg.
    from backtest.settings import BacktestSettings
    # ``fixture_cache`` was already placed at
    # ``<tmp_path>/backtests/baseline-2025-09/store.sqlite`` by the
    # per-window fixture — passing the parent of <window>/ as
    # backtests_root keeps everything in one tree under
    # ``tmp_path/backtests/``.
    settings_obj = BacktestSettings(
        backtests_root               = str(tmp_path / "backtests"),
        ticks_per_day                = ["open"],   # one phase → one tick per session
        failed_tick_abort_ratio      = 1.0,        # never abort in smoke test
        fake_broker_starting_cash    = 100_000.0,
        forward_return_horizons_days = [1],
        ohlcv_warmup_days            = 30,
    )
    _ = fixture_cache  # fixture writes are observed via the path layout above

    # Single-session window inside the canonical ``baseline-2025-09`` range
    # (2025-09-02 → 2025-10-13).  Trimming to a one-day span yields exactly
    # one scheduled tick (one session × one phase = one tick).
    windows = {
        "baseline-2025-09": {
            "start": "2025-09-02",
            "end":   "2025-09-02",
            "notes": "Single-tick slice of the baseline window for smoke test.",
        }
    }
    windows_path = tmp_path / "backtest_windows.json"
    windows_path.write_text(json.dumps(windows))

    watchlist_path = tmp_path / "watchlist.json"
    watchlist_path.write_text(json.dumps({"tickers": ["AAPL"]}))

    # ── Patch LLM agents so no Gemini network call is made ───────────────────
    # Strategy: wrap the pipeline factory functions so every LlmAgent built
    # during the run has its before_model_callback replaced with a shim that
    # returns a synthetic LlmResponse before the real model fires.
    #
    # We patch at the ``orchestrator.pipeline`` module level — patching the
    # two builder functions (``_build_strategist`` and the analyst pool
    # builder) so fresh agent instances inherit the mock callback.

    tickers = ["AAPL"]

    def _patched_build_strategist():
        """Build strategist as SequentialAgent[ContextShim, mock LlmAgent]."""
        from google.adk.agents import LlmAgent, SequentialAgent

        from agents.strategist.agent import _strategist_validation_callback
        from agents.strategist.context_shim import StrategistContextShim
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistDecision without calling Gemini."""
            current_tickers = (
                callback_context.state.get("tickers") or tickers
            )
            return _make_strategist_llm_response(current_tickers)

        llm = LlmAgent(
            name="Strategist",
            model="gemini-2.5-pro",
            instruction=STRATEGIST_INSTRUCTION,
            output_schema=StrategistDecision,
            output_key="strategist_decision",
            after_agent_callback=_strategist_validation_callback,
            before_model_callback=_mock_before,
        )

        return SequentialAgent(
            name="StrategistBranch",
            sub_agents=[StrategistContextShim(), llm],
        )

    def _patched_build_analyst_pool(tick_tickers: list[str]):
        """Build analyst pool with LLM agents short-circuited.

        Phase 9 topology:
        ``SequentialAgent([ParallelAgent([Tech, Social]), Fund branch, News branch])``.

        The Fundamental and News branches are each a
        ``SequentialAgent[FetchAgent, *IsolatedFailureWrapper(...LlmAgent...), JoinerAgent]``.
        Each per-ticker ``LlmAgent`` is named ``NewsAnalyst_<TICKER>`` or
        ``FundamentalAnalyst_<TICKER>`` and expects a ``TickerVerdict`` response.

        The patched factory receives ``tick_tickers`` — the watchlist at the
        current tick — mirroring the signature of ``pipeline._build_analyst_pool``.
        We use that list to build the per-ticker branches so branch count and
        names exactly match the real pipeline.

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

        # Build per-ticker fan-out branches with the tickers for this tick.
        fundamental_branch = build_fundamental_branch(
            h.fundamental_vocabulary, tickers=tick_tickers
        )
        news_branch = build_news_branch(h.news_vocabulary, tickers=tick_tickers)

        def _mock_analyst_before(callback_context, llm_request):
            """Return a synthetic TickerVerdict without calling Gemini.

            ADK sets ``callback_context.agent_name`` to the currently-running
            agent's name — we use that to identify which ticker to emit.
            Each per-ticker LlmAgent is a distinct instance, so ADK calls this
            closure once per branch, scoped to that branch's agent.
            """
            agent_name = getattr(callback_context, "agent_name", "") or ""
            return _make_per_ticker_analyst_llm_response(agent_name)

        def _install_mock_on_branch(branch):
            """Walk ``branch.sub_agents`` and mock every per-ticker LlmAgent.

            Post-Phase-9 parallelism, the branch topology is
            ``Sequential[Fetch, ParallelAgent[IsolatedFailureWrapper×N], Joiner]``,
            so we recurse through ParallelAgent containers as well as the
            ``.inner`` wrapper chain (IsolatedFailureWrapper → RetryingAgentWrapper → LlmAgent).
            """
            for sub in getattr(branch, "sub_agents", []):

                # Recurse into nested ParallelAgent / SequentialAgent containers
                # so the per-ticker fan-out wrapper does not hide the LlmAgents.
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
            name="DeterministicAnalysts",
            sub_agents=[technical, social],
        )

        return SequentialAgent(
            name="AnalystPool",
            sub_agents=[
                parallel_deterministic,
                fundamental_branch,
                news_branch,
            ],
        )

    # ── Patch yfinance so SnapshotterAgent doesn't call the network ───────────
    mock_yf_ticker = MagicMock()
    mock_yf_ticker.history.return_value = MagicMock(
        empty=False,
        __getitem__=lambda self, key: MagicMock(
            iloc=MagicMock(__getitem__=lambda self2, idx: 450.0)
        ),
    )

    with (
        patch(
            "orchestrator.pipeline._build_strategist",
            side_effect=_patched_build_strategist,
        ),
        patch(
            "orchestrator.pipeline._build_analyst_pool",
            side_effect=_patched_build_analyst_pool,
        ),
        patch("yfinance.Ticker", return_value=mock_yf_ticker),
    ):
        runner = Runner(
            settings=settings_obj,
            windows_path=windows_path,
            watchlist_path=watchlist_path,
        )
        # ``generate_ticks`` reads the global settings singleton for
        # ``ticks_per_day`` (not the per-runner ``settings_obj``), so the
        # only reliable way to cap the run at one tick is the runner-level
        # ``tick_limit`` slice — keeps LLM usage at exactly one tick.
        result = runner.run("baseline-2025-09", tick_limit=1)

    # ── Assertions ────────────────────────────────────────────────────────────

    # Terminal status (completed or completed_with_failures — never aborted).
    assert result.status in {"completed", "completed_with_failures"}, (
        f"Unexpected run status: {result.status!r}"
    )

    # ── Cache-wrapper round-trip probe ───────────────────────────────────────
    # Regression guard for the Phase 7.5 bug where ``get_stock_news`` /
    # ``get_company_filings`` failed to forward ``lookback_days`` to the
    # cache provider, raising a TypeError that the analyst fetch layer
    # silently swallowed.  An empty fixture would mask the issue (empty
    # list == "TypeError caught"); the fixture now seeds one news row and
    # one filing, so a non-empty round trip proves the wrapper path is
    # intact end-to-end.
    import asyncio as _asyncio

    from backtest.providers import _store_handle as _sh
    from backtest.providers import filings_cache as _fc  # noqa: F401 — register
    from backtest.providers import news_cache as _nc     # noqa: F401 — register
    from data import get_company_filings, get_stock_news
    from data.registry import set_active_provider as _set_p

    # The Runner restores the original providers on exit (so a crashed run
    # doesn't leak cache state into later calls).  Re-pin to cache for the
    # probe, restoring afterward.  Also re-wire the store handle since the
    # Runner clears it during teardown.
    _sh.set_store(CachedDataStore(fixture_cache))
    _restores = [_set_p("news", "cache"), _set_p("filings", "cache")]

    try:
        _probe_as_of = datetime.fromisoformat("2025-09-02T20:00:00+00:00")
        _news_probe  = _asyncio.run(get_stock_news("AAPL", as_of=_probe_as_of))
        _files_probe = _asyncio.run(get_company_filings("AAPL", as_of=_probe_as_of))
    finally:
        for _restore in _restores:
            _restore()

    assert _news_probe, (
        "get_stock_news returned empty for AAPL inside the smoke window — "
        "the cache wrapper path is broken (likely a missing lookback_days "
        "forward, swallowed by the analyst fetch try/except)."
    )
    assert _files_probe, (
        "get_company_filings returned empty for AAPL inside the smoke window — "
        "see above; cache wrapper path is broken."
    )

    # Manifest exists and has the right run_id.
    manifest_path = result.run_dir / "manifest.json"
    assert manifest_path.exists(), "manifest.json not written"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["run_id"] == result.run_id

    # Traces directory exists (driver writes one .json file per tick).
    traces_dir = result.run_dir / "traces"
    assert traces_dir.exists(), "traces/ directory not created"
    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1, "No trace files written"

    # Equity curve PNG produced by the reporting module.
    equity_curve = result.run_dir / "report" / "equity_curve.png"
    assert equity_curve.exists(), "report/equity_curve.png not produced"

    # Metrics markdown produced — and the key headline metric (total return)
    # must be a valid percentage, not NaN.  Sharpe can be NaN in a short
    # zero-trade window where portfolio variance is zero; that is acceptable
    # and expected for a single-tick smoke run.
    metrics_path = result.run_dir / "report" / "metrics.md"
    assert metrics_path.exists(), "report/metrics.md not produced"
    metrics_text = metrics_path.read_text(encoding="utf-8")

    # Verify the file is not empty and contains the expected section header.
    assert "# Backtest metrics" in metrics_text, (
        f"metrics.md missing header:\n{metrics_text}"
    )

    # Total return must be present and must be a valid percentage (not NaN).
    import re
    total_return_match = re.search(r"Total return.*\*\*([^*]+)\*\*", metrics_text)
    assert total_return_match is not None, (
        f"metrics.md missing Total return line:\n{metrics_text}"
    )
    assert "nan" not in total_return_match.group(1).lower(), (
        f"Total return is NaN in metrics.md:\n{metrics_text}"
    )

    # §5.4 — audit telemetry assertions.
    # The manifest must declare audit_complete=True, meaning every scheduled
    # tick produced its .tick.json telemetry record.
    assert manifest.get("audit_complete") is True, (
        f"manifest.audit_complete is not True: {manifest.get('audit_complete')!r}"
    )

    # Every audit record must have all five tripwire flags == False.
    # A fired tripwire indicates a potential point-in-time data leak.
    audit_dir = result.run_dir / "audit"
    assert audit_dir.exists(), "audit/ directory not created"

    audit_files = list(audit_dir.glob("*.tick.json"))
    assert len(audit_files) >= 1, "No audit telemetry records written"

    # Tripwires that indicate a definitive point-in-time data leak.
    # Advisory tripwires (``*_advisory`` suffix) are intentionally excluded:
    # - ``open_tick_sameday_bar_advisory``: the store's inclusive-range query
    #   (end=as_of.date()) surfaces the same-day bar at the raw read level,
    #   but price_history_cache.fetch strips it before any analyst receives it.
    # - ``midnight_utc_timestamps_seen_advisory``: date-only sources promote
    #   all timestamps to midnight UTC — steady-state behaviour, not a leak.
    # Both advisory keys are benign by design; use ACTIONABLE_TRIPWIRES from
    # backtest.audit.tripwires for programmatic filtering.
    DEFINITIVE_LEAK_TRIPWIRES = {
        "wall_clock_fallback_fired",
        "any_filter_key_after_as_of",
        "missing_timestamp_rows_seen",
    }

    for audit_file in audit_files:
        record    = json.loads(audit_file.read_text(encoding="utf-8"))
        tripwires = record.get("tripwires", {})
        fired = {
            name: val
            for name, val in tripwires.items()
            if name in DEFINITIVE_LEAK_TRIPWIRES and val is not False
        }
        assert not fired, (
            f"Definitive-leak tripwire(s) fired in {audit_file.name}: {fired}"
        )

    # ── user:positions persistence assertion (Spec B Band 5) ────────────────
    # The session sqlite lives at <run_dir>/session.sqlite.  Open a fresh
    # DatabaseSessionService, list sessions for the backtest app_name, and
    # assert the last tick's session carries a non-empty user:positions.
    import asyncio as _asyncio_b5
    from google.adk.sessions import DatabaseSessionService as _DSS

    _session_sqlite = result.run_dir / "session.sqlite"

    # Both guards are hard assertions: a passing smoke run MUST produce a
    # session sqlite and MUST have at least one session inside it.  Silently
    # skipping these checks would give a green result with zero signal on the
    # spec-required user:positions persistence guarantee.
    assert _session_sqlite.exists(), (
        f"Smoke run did not create session sqlite at {_session_sqlite}; "
        "DatabaseSessionService wiring is broken or run_dir is wrong."
    )

    _svc = _DSS(db_url=f"sqlite+aiosqlite:///{_session_sqlite}")
    _app_name = "StockBot-backtest-baseline-2025-09"

    _sessions = _asyncio_b5.run(_svc.list_sessions(app_name=_app_name, user_id="stockbot"))

    # The smoke test runs exactly one tick — there must be exactly one session.
    assert _sessions and _sessions.sessions, (
        "DatabaseSessionService.list_sessions returned no sessions after smoke run; "
        f"app_name={_app_name!r}, user_id='stockbot'. "
        "Either the session was never created or list_sessions is broken."
    )

    _last_sid = _sessions.sessions[-1].id
    _last_session = _asyncio_b5.run(
        _svc.get_session(app_name=_app_name, user_id="stockbot", session_id=_last_sid)
    )
    assert _last_session is not None, "last tick session must be fetchable"
    _user_positions = _last_session.state.get("user:positions")
    assert isinstance(_user_positions, dict), (
        f"user:positions must be a dict in the last tick session; "
        f"got {type(_user_positions).__name__!r}"
    )
    assert len(_user_positions) >= 1, (
        "user:positions must be non-empty after the smoke run; "
        "the executor's thesis-writer callback did not persist any position"
    )

    # Phase 7 — non-Social analysts that can produce signal from the minimal
    # fixture must emit a non-is_no_data verdict.  Social explicitly soft-fails
    # per spec decision 9.3.  SmartMoney is also excluded here: the fixture
    # cache has no filing data (politician_trades / notable_holders), so
    # is_no_data=True is correct behaviour for that analyst on this fixture.
    # The full four-analyst assertion (including SmartMoney) lives in
    # test_no_silent_zero_features (since removed) used to cover SmartMoney.
    #
    # Verdicts are not stored in the manifest; they live in trace files under
    # the "04_digest" section (ticker_evidence_objects).  With a single-tick
    # window there is exactly one trace file, so we sample it directly.
    non_social_analysts = {"technical", "fundamental", "news"}

    trace_files_sorted = sorted(trace_files)
    sample_trace_file  = trace_files_sorted[len(trace_files_sorted) // 2]
    sample_trace       = json.loads(sample_trace_file.read_text(encoding="utf-8"))
    digest_section     = sample_trace.get("04_digest") or {}
    digest_data: list  = digest_section.get("data") or []

    # Only assert if the digest was produced — the single-tick window with a
    # single AAPL ticker should always produce one.
    if digest_data:
        for ticker_evidence in digest_data:
            ticker      = ticker_evidence.get("ticker", "<unknown>")
            per_analyst = ticker_evidence.get("per_analyst") or {}

            for analyst in non_social_analysts:
                evidence = per_analyst.get(analyst)
                if evidence is None:
                    # Analyst absent from pool — skip rather than hard-fail
                    # so the smoke test is not brittle against pool composition
                    # changes during development.
                    continue

                verdict = (evidence.get("verdict") or {})
                assert verdict.get("is_no_data") is not True, (
                    f"ticker={ticker}: '{analyst}' silently degraded to "
                    f"is_no_data=True in {sample_trace_file.name} — "
                    "Phase 2/4 gap detected by smoke test."
                )
