"""Smoke test — replay the SVB window and assert the four-extractor verdict
matrix has no silent zero-features.

Marked ``@pytest.mark.slow`` + ``@pytest.mark.integration`` to keep the
default test run fast.  Requires:

1. A filled SVB-window cache at the path declared in
   ``config/backtest_settings.json`` (run ``scripts.backtest_fetch
   --window svb-stress-2023-03`` first).
2. Gemini credentials are NOT required — LLM agents are short-circuited via
   the standard ``before_model_callback`` mocks used in the end-to-end smoke
   test.

What this asserts
-----------------
- Every non-Social analyst (technical, fundamental, news, smart_money) must
  produce a non-``is_no_data`` verdict on at least the middle scheduled tick.
- The ``relative_strength_vs_spy_*`` feature family introduced by Fix C must
  be present and have at least one non-zero value in the technical evidence.

The assertions read from the ``"04_digest"`` trace section, which contains
``ticker_evidence_objects`` — a list of ``TickerEvidence`` dicts each holding
``per_analyst`` keyed by analyst name.  This is the same data the strategist
sees, making it the definitive "did the extractor deliver signal?" surface.

Phase 9 update
--------------
The Fundamental and News analysts are now per-ticker fan-out
``SequentialAgent`` branches.  Their inner ``LlmAgent`` instances are named
``FundamentalAnalyst_<TICKER>`` / ``NewsAnalyst_<TICKER>`` and emit
``TickerVerdict`` (single-ticker JSON), not ``VerdictBatch``.  The
``_make_per_ticker_analyst_llm_response`` helper and the
``_install_mock_on_branch`` walker replace the old
``fundamental.before_model_callback = ...`` pattern.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — synthetic LLM response payloads (mirrors end-to-end smoke test)
# ---------------------------------------------------------------------------

def _make_strategist_llm_response(tickers: list[str]):
    """Return a synthetic ``LlmResponse`` containing a valid ``StrategistDecision``.

    Identical shim to the one in ``test_end_to_end_smoke`` — kept local so
    this file is self-contained and can be run independently.

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
            "ticker":           t,
            "preferred_weight": 0.0,
            "conviction":       0.5,
            "rationale":        "SVB smoke test neutral stance.",
        }
        for t in tickers
    ]
    decision = {
        "stances":        stances,
        "target_weights": {t: 0.0 for t in tickers},
        "decision_tag":   "svb_smoke_hold",
        "reasoning":      "SVB smoke test run — no live data.",
        "updated_thesis": "Awaiting real signal.",
        "confidence":     0.5,
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
    extracted from the agent name by stripping the well-known prefix.

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

    # Strip well-known prefixes to recover the ticker symbol.
    ticker = agent_name
    for prefix in ("NewsAnalyst_", "FundamentalAnalyst_"):
        if agent_name.startswith(prefix):
            ticker = agent_name[len(prefix):]
            break

    verdict = {
        "ticker":      ticker,
        "lean":        "neutral",
        "magnitude":   0.0,
        "confidence":  0.5,
        "rationale":   "SVB smoke test stub.",
        "key_factors": [],
        "is_no_data":  False,
    }
    return LlmResponse(
        content=genai_types.Content(
            parts=[genai_types.Part.from_text(text=json.dumps(verdict))]
        )
    )


def _load_digest(trace_file: Path) -> list[dict]:
    """Read the ``"04_digest"`` section from a trace file.

    Parameters
    ----------
    trace_file:
        Path to one ``*.json`` trace file produced by the driver.

    Returns
    -------
    list[dict]
        The ``data`` payload from the ``"04_digest"`` section — a list of
        ``TickerEvidence`` dicts.  Returns an empty list if the section is
        absent.
    """
    raw = json.loads(trace_file.read_text(encoding="utf-8"))
    digest_section = raw.get("04_digest")
    if digest_section is None:
        return []
    return digest_section.get("data") or []


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
def test_no_silent_zero_features_on_svb_window(tmp_path: Path) -> None:
    """Replay the SVB-stress window; assert no non-Social analyst silently degrades.

    Uses the real SVB golden cache from ``config/backtest_settings.json`` but
    writes run artefacts under ``tmp_path`` so it does not pollute
    ``backtests/runs/``.  LLM agents are short-circuited via the same
    ``before_model_callback`` shims as ``test_end_to_end_smoke``.

    Asserts:
    - Every non-Social analyst emits a verdict with ``is_no_data=False`` for
      every ticker in the digest of the middle scheduled tick.
    - The ``relative_strength_vs_spy_*`` feature family is present with at
      least one non-zero value (Fix C — reference_prices plumbing).
    """
    from backtest.runner import Runner

    # ── Build a settings override that keeps the real cache but redirects
    #    runs to a temporary directory so this test is idempotent. ──────────
    import json as _json
    from pathlib import Path as _Path

    # Per-window layout: every artefact for this window lives under
    # ``<backtests_root>/svb-stress-2023-03/``.  We redirect the whole
    # backtests root into ``tmp_path`` and symlink the per-window directory
    # to the real one so the test can read the live svb cache without
    # mutating the project tree's ``runs/`` subtree.
    real_settings   = _json.loads(_Path("config/backtest_settings.json").read_text())
    real_backtests  = _Path(real_settings["backtests_root"]).resolve()
    tmp_backtests   = tmp_path / "backtests"
    tmp_backtests.mkdir()
    # Symlink the live svb window into the tmp tree so cache reads hit the
    # frozen golden cache while ``runs/`` writes land in ``tmp_path``.
    (tmp_backtests / "svb-stress-2023-03").symlink_to(
        real_backtests / "svb-stress-2023-03",
        target_is_directory=True,
    )

    override_settings = {**real_settings, "backtests_root": str(tmp_backtests)}
    override_settings_path = tmp_path / "backtest_settings.json"
    override_settings_path.write_text(_json.dumps(override_settings))

    # Discover the live watchlist so the LLM stubs know which tickers to cover.
    watchlist = _json.loads(_Path("config/watchlist.json").read_text())["tickers"]

    # ── Seed synthetic SPY bars into the real SVB cache ──────────────────────
    # The technical extractor needs state["reference_prices"]["SPY"] to compute
    # relative_strength_vs_spy_* features (Fix C).  Those bars are normally
    # written by scripts.backtest_fetch._fill_reference_ohlcv, but that step
    # requires a live yfinance call.  We write synthetic flat bars here so the
    # test is self-contained and network-free.  The store uses
    # on_conflict_do_nothing, so re-running is idempotent.
    from backtest.cache.store import CachedDataStore
    from data.models import OHLCBar

    # Resolve the per-window cache via the helper rather than hand-rolling
    # the path — keeps the test honest about the live layout.
    from backtest.settings import (
        cache_path_for_window,
        load_backtest_settings_from,
    )
    _real_settings_obj = load_backtest_settings_from(
        _Path("config/backtest_settings.json")
    )
    _cache_path = cache_path_for_window(_real_settings_obj, "svb-stress-2023-03")
    _store = CachedDataStore(_cache_path)

    # SVB window is 2023-03-06 to 2023-04-07.  Include 30 days of warm-up
    # so the runner's _seed_reference_prices call finds bars covering the
    # full [window.start - warmup_days, window.end] range.
    _spy_start = datetime(2023, 2, 4, tzinfo=UTC)  # ~30 days before window start
    _spy_end   = datetime(2023, 4, 7, tzinfo=UTC)

    _spy_bars = []
    _day = _spy_start
    while _day <= _spy_end:
        _spy_bars.append(OHLCBar(
            timestamp=_day,
            open=400.0,
            high=402.0,
            low=398.0,
            close=401.0,
            volume=80_000_000,
        ))
        _day += timedelta(days=1)

    _store.write_ohlcv("SPY", _spy_bars)

    # ── Build pipeline-factory patches (identical strategy to end-to-end smoke).
    def _patched_build_strategist():
        """Build strategist with a mock before_model_callback.

        ``_composite_before_callback`` was removed in A2.1 — the strategist
        is constructed here without a ``before_agent_callback``, matching the
        production path in ``build_strategist()`` which also omits it.
        """
        from google.adk.agents import LlmAgent

        from agents.strategist.agent import _strategist_validation_callback
        from agents.strategist.prompts import STRATEGIST_INSTRUCTION
        from agents.strategist.schema import StrategistDecision

        def _mock_before(callback_context, llm_request):
            """Return a synthetic StrategistDecision without calling Gemini."""
            current_tickers = callback_context.state.get("tickers") or watchlist
            return _make_strategist_llm_response(current_tickers)

        return LlmAgent(
            name="Strategist",
            model="gemini-2.5-pro",
            instruction=STRATEGIST_INSTRUCTION,
            output_schema=StrategistDecision,
            output_key="strategist_decision",
            after_agent_callback=_strategist_validation_callback,
            before_model_callback=_mock_before,
        )

    def _patched_build_analyst_pool(tick_tickers: list[str]):
        """Build analyst pool with LLM analysts short-circuited.

        Phase 9 topology: the Fundamental and News analysts are per-ticker
        fan-out ``SequentialAgent`` branches.  Each per-ticker ``LlmAgent``
        is named ``FundamentalAnalyst_<TICKER>`` / ``NewsAnalyst_<TICKER>``
        and expects a ``TickerVerdict`` response (not a ``VerdictBatch``).

        The patched factory receives ``tick_tickers`` — the watchlist at the
        current tick — mirroring the signature of ``pipeline._build_analyst_pool``.

        The mock callback is installed by walking each branch's ``sub_agents``
        and digging through the
        ``IsolatedFailureWrapper → RetryingAgentWrapper → LlmAgent`` chain.

        Parameters
        ----------
        tick_tickers:
            Ticker list for the current tick, forwarded from ``build_pipeline``.
        """
        from google.adk.agents import LlmAgent, ParallelAgent

        from agents.analysts.fundamental.agent import build_fundamental_branch
        from agents.analysts.heuristics import load_heuristics
        from agents.analysts.news.agent import build_news_branch
        from agents.analysts.smart_money.agent import _build_smart_money_analyst
        from agents.analysts.social.agent import _build_social_analyst
        from agents.analysts.technical.agent import _build_technical_analyst

        h = load_heuristics()

        # Deterministic analysts — no LLM involved.
        technical   = _build_technical_analyst(h.technical)
        social      = _build_social_analyst(h.social)
        smart_money = _build_smart_money_analyst(h.smart_money)

        # Build per-ticker fan-out branches with the tickers for this tick.
        fundamental = build_fundamental_branch(
            h.fundamental_vocabulary, tickers=tick_tickers
        )
        news = build_news_branch(h.news_vocabulary, tickers=tick_tickers)

        def _mock_analyst_before(callback_context, llm_request):
            """Return a synthetic TickerVerdict without calling Gemini.

            The ticker is recovered from the agent name so each branch
            receives its own correctly-labelled verdict.
            """
            agent_name = getattr(callback_context, "agent_name", "") or ""
            return _make_per_ticker_analyst_llm_response(agent_name)

        def _install_mock_on_branch(branch):
            """Set ``before_model_callback`` on every per-ticker LlmAgent in branch.

            Post-Phase-9 parallelism, the branch topology is
            ``Sequential[Fetch, ParallelAgent[IsolatedFailureWrapper×N], Joiner]``,
            so we recurse through ParallelAgent / SequentialAgent containers as
            well as the ``.inner`` wrapper chain
            (``IsolatedFailureWrapper → RetryingAgentWrapper → LlmAgent``).
            """
            for sub in getattr(branch, "sub_agents", []):

                # Recurse into nested container agents so the per-ticker fan-out
                # wrapper does not hide the LlmAgents from the mock walker.
                if getattr(sub, "sub_agents", None):
                    _install_mock_on_branch(sub)
                    continue

                # Traverse wrapper chain to reach the inner LlmAgent.
                node = sub
                while node is not None and not isinstance(node, LlmAgent):
                    node = getattr(node, "inner", None)
                if isinstance(node, LlmAgent) and node.name.startswith(
                    ("NewsAnalyst_", "FundamentalAnalyst_")
                ):
                    node.before_model_callback = _mock_analyst_before

        _install_mock_on_branch(fundamental)
        _install_mock_on_branch(news)

        return ParallelAgent(
            name="AnalystPool",
            sub_agents=[technical, fundamental, news, social, smart_money],
        )

    # Patch yfinance so SnapshotterAgent doesn't hit the network.
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
        # ``Runner`` now takes a pre-loaded ``BacktestSettings`` instance via
        # ``settings=`` rather than a file path — tests load the sandboxed JSON
        # themselves and inject the resulting model.
        from backtest.settings import load_backtest_settings_from

        runner = Runner(
            settings=load_backtest_settings_from(override_settings_path),
            windows_path=Path("config/backtest_windows.json"),
            watchlist_path=Path("config/watchlist.json"),
        )
        result = runner.run("svb-stress-2023-03")

    # ── Locate traces and sample the middle tick ───────────────────────────
    traces_dir  = Path(result.run_dir) / "traces"
    trace_files = sorted(traces_dir.glob("*.json"))
    assert trace_files, "no trace files produced — did the SVB cache fill complete?"

    # Sample the middle tick for a representative "steady-state" assertion.
    sample_file = trace_files[len(trace_files) // 2]
    digest      = _load_digest(sample_file)

    assert digest, (
        f"'04_digest' section missing or empty in {sample_file.name}. "
        "The strategist's evidence-view agent may not have run."
    )

    # ── is_no_data assertion ──────────────────────────────────────────────────
    # For each watchlist ticker in the digest, every non-Social analyst must
    # have produced a real verdict.  Social is explicitly expected to be
    # is_no_data=True throughout v1 per spec decision 9.3.
    non_social_analysts = {"technical", "fundamental", "news", "smart_money"}

    for ticker_evidence in digest:
        ticker      = ticker_evidence.get("ticker", "<unknown>")
        per_analyst = ticker_evidence.get("per_analyst") or {}

        for analyst in non_social_analysts:
            evidence = per_analyst.get(analyst)
            assert evidence is not None, (
                f"ticker={ticker}: '{analyst}' evidence missing from digest — "
                "analyst may not have been included in the pool."
            )
            verdict: dict = evidence.get("verdict") or {}
            assert verdict.get("is_no_data") is not True, (
                f"ticker={ticker}: '{analyst}' silently degraded to "
                f"is_no_data=True on {sample_file.name}. "
                "Check the Phase 2/4 extractor for this analyst."
            )

    # ── Fix C: relative_strength_vs_spy feature family ───────────────────────
    # The reference_prices plumbing (Phase 5) seeds SPY + sector ETF prices
    # before the analyst pool runs so the technical extractor can emit
    # relative_strength_vs_spy_{w}d features.  Assert that at least one
    # ticker/window combination lit up a non-zero value.
    spy_feature_seen = False

    for ticker_evidence in digest:
        per_analyst  = ticker_evidence.get("per_analyst") or {}
        tech_evidence = per_analyst.get("technical")
        if tech_evidence is None:
            continue

        features = tech_evidence.get("features") or {}
        spy_keys = [k for k in features if k.startswith("relative_strength_vs_spy_")]

        # Record any non-zero hit across all window lengths.
        if any(features[k] != 0.0 for k in spy_keys):
            spy_feature_seen = True
            break

    assert spy_feature_seen, (
        "No non-zero relative_strength_vs_spy_* feature found in the middle-tick "
        "digest. Fix C (reference_prices plumbing, Phase 5) may not be wired "
        "into the cache providers for the SVB window."
    )
