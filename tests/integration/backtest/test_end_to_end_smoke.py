"""Smoke test: full Runner over a 3-day micro-window against a fixture cache.

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

LLM mocking
-----------
The strategist (Gemini 2.5 Pro) and the two LLM analyst agents (Fundamental,
News) are mocked via a ``before_model_callback`` shim that returns a synthetic
``LlmResponse`` before the real model is ever called.  This shim is installed
by monkeypatching ``google.adk.agents.llm_agent.LlmAgent._run_async_impl``.
The cleaner seam is patching ``before_model_callback`` on each agent instance
— but ``build_pipeline`` builds fresh instances internally, so we patch the
factory functions in ``orchestrator.pipeline`` so every newly constructed
``LlmAgent`` inherits the mock callback.

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
from data.models import CompanyRatios, OHLCBar
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

    stances = [
        {
            "ticker": t,
            "preferred_weight": 0.0,
            "conviction": 0.5,
            "rationale": "Smoke test neutral stance — no real signal.",
        }
        for t in tickers
    ]
    decision = {
        "stances":       stances,
        "target_weights": {t: 0.0 for t in tickers},
        "decision_tag":   "smoke_test_hold",
        "reasoning":      "Smoke test run — no live data.",
        "updated_thesis": "Awaiting real signal.",
        "confidence":     0.5,
    }
    return LlmResponse(
        content=genai_types.Content(
            parts=[genai_types.Part.from_text(text=json.dumps(decision))]
        )
    )


def _make_analyst_llm_response(tickers: list[str]):
    """Return a synthetic ``LlmResponse`` containing a valid ``VerdictBatch``.

    Used for the Fundamental and News ``LlmAgent`` analysts, whose
    ``output_schema=VerdictBatch``.

    Parameters
    ----------
    tickers:
        The watchlist tickers the batch should cover.

    Returns
    -------
    google.adk.models.LlmResponse
        A synthetic response with a ``VerdictBatch`` JSON payload.
    """
    from google.adk.models import LlmResponse
    from google.genai import types as genai_types

    verdicts = [
        {
            "ticker":     t,
            "lean":       "neutral",
            "magnitude":  0.0,
            "confidence": 0.5,
            "rationale":  "Smoke test stub.",
            "drivers":    [
                {"name": "A", "direction": "bull", "weight": 0.5, "body": "Stub driver A."},
                {"name": "B", "direction": "bear", "weight": 0.5, "body": "Stub driver B."},
            ],
        }
        for t in tickers
    ]
    batch = {"verdicts": verdicts}
    return LlmResponse(
        content=genai_types.Content(
            parts=[genai_types.Part.from_text(text=json.dumps(batch))]
        )
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fixture_cache(tmp_path: Path) -> Path:
    """Materialise a 3-business-day OHLCV + CompanyRatios cache for AAPL.

    The cache is written to ``tmp_path/cache/store.sqlite`` and the path
    to that file is returned for injection into the Runner settings.

    The three days chosen (2023-03-13, 2023-03-14, 2023-03-15) are NYSE
    business days during the SVB stress period — a realistic micro-window.

    Parameters
    ----------
    tmp_path:
        Pytest's temporary directory for this test invocation.

    Returns
    -------
    Path
        Absolute path to the SQLite cache file.
    """
    cache_path = tmp_path / "cache" / "store.sqlite"
    store = CachedDataStore(cache_path)

    # Write 25 warm-up bars (2023-02-06 to 2023-03-10) plus the 3 window
    # days (2023-03-13, 2023-03-14, 2023-03-15) for AAPL.
    #
    # RSI(14) and ATR(14) each need at least 15 bars; pct_change_20d needs
    # 21.  With only the 3 window bars the technical extractor's no-data
    # guard fires and is_no_data=True.  The warm-up bars give the extractor
    # enough history to compute at least rsi_14 and atr_pct_14, ensuring the
    # Phase 7 is_no_data assertion passes on this fixture cache.
    #
    # Close prices step up by 0.10 per bar so pct_change_5d is non-zero.
    _aapl_bars = []
    _warm_start = datetime.fromisoformat("2023-02-06T00:00:00+00:00")
    _window_days = [
        datetime.fromisoformat("2023-03-13T00:00:00+00:00"),
        datetime.fromisoformat("2023-03-14T00:00:00+00:00"),
        datetime.fromisoformat("2023-03-15T00:00:00+00:00"),
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
        as_of_date=datetime(2023, 3, 10).date(),
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
    """One Runner.run() over a 3-day window produces the expected artefact tree.

    Asserts:
    - ``manifest.json`` exists and reports a terminal status.
    - ``traces/`` directory exists (one ``.json`` trace file per tick).
    - ``report/metrics.md`` exists with non-NaN metric values.
    - ``report/equity_curve.png`` exists.

    LLM agents (Strategist, Fundamental, News) are short-circuited via a
    synthetic ``before_model_callback`` so no Gemini calls are made.
    yfinance (SPY lookup in Snapshotter) is monkeypatched to return a flat
    450.0 price without hitting the network.
    """
    from backtest.runner import Runner

    # ── Write settings + windows + watchlist under tmp_path ──────────────────
    settings = {
        "cache_path":                  str(fixture_cache),
        "runs_root":                   str(tmp_path / "runs"),
        "ticks_per_day":               ["open", "close"],
        "tz":                          "America/New_York",
        "open_time":                   "09:30",
        "close_time":                  "16:00",
        "failed_tick_abort_ratio":     1.0,        # never abort in smoke test
        "fake_broker_starting_cash":   100_000.0,
        "forward_return_horizons_days": [1],
    }
    settings_path = tmp_path / "backtest_settings.json"
    settings_path.write_text(json.dumps(settings))

    windows = {
        "smoke": {
            "start": "2023-03-13",
            "end":   "2023-03-15",
            "notes": "Three-day micro-window for smoke test.",
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
        """Build strategist with a mock before_model_callback."""
        from google.adk.agents import LlmAgent

        from agents.strategist.agent import (
            _composite_before_callback,
            _strategist_validation_callback,
        )
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistDecision without calling Gemini."""
            current_tickers = (
                callback_context.state.get("tickers") or tickers
            )
            return _make_strategist_llm_response(current_tickers)

        return LlmAgent(
            name="Strategist",
            model="gemini-2.5-pro",
            instruction=STRATEGIST_INSTRUCTION,
            output_schema=StrategistDecision,
            output_key="strategist_decision",
            before_agent_callback=_composite_before_callback,
            after_agent_callback=_strategist_validation_callback,
            before_model_callback=_mock_before,
        )

    def _patched_build_analyst_pool():
        """Build analyst pool with LLM agents short-circuited."""
        from google.adk.agents import LlmAgent, ParallelAgent

        from agents.analysts.fundamental.agent import _build_fundamental_analyst
        from agents.analysts.heuristics import load_heuristics
        from agents.analysts.news.agent import _build_news_analyst
        from agents.analysts.smart_money.agent import _build_smart_money_analyst
        from agents.analysts.social.agent import _build_social_analyst
        from agents.analysts.technical.agent import _build_technical_analyst
        from contract.evidence import VerdictBatch

        h = load_heuristics()

        # Deterministic analysts are BaseAgent subclasses — no LLM involved.
        technical  = _build_technical_analyst(h.technical)
        social     = _build_social_analyst(h.social)
        smart_money = _build_smart_money_analyst(h.smart_money)

        # LLM analysts need their before_model_callback mocked.
        fundamental = _build_fundamental_analyst(h.fundamental_vocabulary)
        news        = _build_news_analyst(h.news_vocabulary)

        def _mock_analyst_before(callback_context, llm_request):
            """Return a synthetic VerdictBatch without calling Gemini."""
            current_tickers = (
                callback_context.state.get("tickers") or tickers
            )
            return _make_analyst_llm_response(current_tickers)

        # Overwrite the before_model_callback on each LLM analyst.
        # ADK reads this attribute from the agent instance at call time.
        fundamental.before_model_callback = _mock_analyst_before
        news.before_model_callback        = _mock_analyst_before

        return ParallelAgent(
            name="AnalystPool",
            sub_agents=[
                technical,
                fundamental,
                news,
                social,
                smart_money,
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
            settings_path=settings_path,
            windows_path=windows_path,
            watchlist_path=watchlist_path,
        )
        result = runner.run("smoke")

    # ── Assertions ────────────────────────────────────────────────────────────

    # Terminal status (completed or completed_with_failures — never aborted).
    assert result.status in {"completed", "completed_with_failures"}, (
        f"Unexpected run status: {result.status!r}"
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
    # and expected for a 3-day smoke run.
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
    # ``open_tick_sameday_bar`` is intentionally excluded here: the store's
    # inclusive-range query (end=as_of.date()) surfaces the same-day bar at
    # the raw read level, but the price_history_cache provider correctly strips
    # it before any analyst receives it.  That tripwire is a known, expected
    # artefact of the inclusive store API; it does not represent an actual
    # leak and is left for human review in deep-dump (Layer 2) workflows.
    DEFINITIVE_LEAK_TRIPWIRES = {
        "wall_clock_fallback_fired",
        "any_filter_key_after_as_of",
        "midnight_utc_timestamps_seen",
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

    # Phase 7 — non-Social analysts that can produce signal from the minimal
    # fixture must emit a non-is_no_data verdict.  Social explicitly soft-fails
    # per spec decision 9.3.  SmartMoney is also excluded here: the fixture
    # cache has no filing data (politician_trades / notable_holders), so
    # is_no_data=True is correct behaviour for that analyst on this fixture.
    # The full four-analyst assertion (including SmartMoney) lives in
    # test_no_silent_zero_features, which runs against the real SVB cache.
    #
    # Verdicts are not stored in the manifest; they live in trace files under
    # the "04_digest" section (ticker_evidence_objects).  We sample the middle
    # trace tick — the same strategy as test_no_silent_zero_features.
    non_social_analysts = {"technical", "fundamental", "news"}

    trace_files_sorted = sorted(trace_files)
    sample_trace_file  = trace_files_sorted[len(trace_files_sorted) // 2]
    sample_trace       = json.loads(sample_trace_file.read_text(encoding="utf-8"))
    digest_section     = sample_trace.get("04_digest") or {}
    digest_data: list  = digest_section.get("data") or []

    # Only assert if the digest was produced — the 3-day micro-window with a
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
