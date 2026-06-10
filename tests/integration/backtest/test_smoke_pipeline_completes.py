"""Smoke concern 1 — pipeline completes without raising.

Asserts:
- The ``Runner.run()`` call returned a terminal status (completed or
  completed_with_failures — never aborted).
- The manifest was written and carries the expected run_id.
- The cache-wrapper round-trip (get_stock_news / get_company_filings) returns
  non-empty results — regression guard for the Phase 7.5 bug where
  ``lookback_days`` was not forwarded to the cache provider, causing a
  TypeError that the analyst fetch layer silently swallowed.
- ``degradation_check(state)`` passes: no analyst domain silently degraded to
  ``is_no_data=True`` on the happy path (social and smart_money exempted per
  spec decisions 9.3 and the fixture's missing filing data, respectively).

Uses the module-scoped ``smoke_result`` fixture from conftest.py so the
expensive ADK pipeline run executes exactly once across all four per-concern
smoke-test modules.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime

import pytest

from backtest.cache.store import CachedDataStore


@pytest.mark.slow
def test_smoke_pipeline_completes(smoke_result, degradation_check) -> None:
    """Runner.run() over one tick must complete and produce a valid manifest.

    Also verifies no silent degradation on the happy path, and that the
    cache-wrapper round-trip resolves non-empty news and filing rows.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``
        and helpers for inspecting the run's artefacts.
    degradation_check:
        Function fixture from tests/conftest.py that calls
        ``assert_no_silent_degradation`` — raises on any ``is_no_data=True``
        verdict unless the domain is listed in ``allow_degradation``.
    """
    result = smoke_result.result

    # ── Terminal status ────────────────────────────────────────────────────────
    # The run must not abort.  "completed_with_failures" is acceptable for a
    # smoke run (the artefact tree is still produced); "aborted" is not.
    assert result.status in {"completed", "completed_with_failures"}, (
        f"Unexpected run status: {result.status!r}"
    )

    # ── Manifest exists and records the correct run_id ─────────────────────────
    manifest_path = result.run_dir / "manifest.json"
    assert manifest_path.exists(), "manifest.json not written"

    manifest = json.loads(manifest_path.read_text())
    assert manifest["run_id"] == result.run_id, (
        f"manifest.run_id={manifest['run_id']!r} does not match "
        f"result.run_id={result.run_id!r}"
    )

    # ── Cache-wrapper round-trip probe ─────────────────────────────────────────
    # Regression guard for Phase 7.5: the cache wrappers for news and filings
    # must forward ``lookback_days`` correctly so a non-empty fixture row is
    # returned.  An empty result here means the wrapper path is broken (e.g. a
    # TypeError swallowed by the analyst fetch try/except).
    from backtest.providers import _store_handle as _sh
    from backtest.providers import filings_cache as _fc  # noqa: F401 — register
    from backtest.providers import news_cache as _nc     # noqa: F401 — register
    from data import get_company_filings, get_stock_news
    from data.registry import set_active_provider as _set_p

    # The Runner restores the original providers on exit.  Re-pin to the
    # cache provider for the probe, then restore afterward.  Also re-wire the
    # store handle since the Runner clears it during teardown.
    _sh.set_store(CachedDataStore(smoke_result.cache_path))
    _restores = [_set_p("news", "cache"), _set_p("filings", "cache")]

    try:
        probe_as_of  = datetime.fromisoformat("2025-09-02T20:00:00+00:00")
        news_probe   = asyncio.run(get_stock_news("AAPL", as_of=probe_as_of))
        files_probe  = asyncio.run(get_company_filings("AAPL", as_of=probe_as_of))
    finally:
        for restore in _restores:
            restore()

    assert news_probe, (
        "get_stock_news returned empty for AAPL inside the smoke window — "
        "the cache wrapper path is broken (likely a missing lookback_days "
        "forward, swallowed by the analyst fetch try/except)."
    )
    assert files_probe, (
        "get_company_filings returned empty for AAPL inside the smoke window — "
        "see above; cache wrapper path is broken."
    )

    # ── No silent degradation on the happy path ────────────────────────────────
    # Social degrades legitimately per spec decision 9.3 (no live social feed
    # in the fixture cache).  SmartMoney also degrades legitimately — the
    # fixture has no politician-trades / notable-holder filing data.
    state = smoke_result.get_last_session_state()
    degradation_check(state, allow_degradation=("social", "smart_money"))
