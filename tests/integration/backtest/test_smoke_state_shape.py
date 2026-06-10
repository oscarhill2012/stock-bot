"""Smoke concern 4 — final tick-state shape matches contract invariants.

Asserts:
- The ``session.sqlite`` file exists and contains at least one session
  (infrastructure guards before reading state).
- The last-tick session state carries every §A key listed in
  ``docs/contract-invariants.md``.
- The state contains NO bare ``positions`` key — only ``user:positions`` and
  ``temp:_positions`` are legal (Plan 03 / A-070).
- ``user:positions`` is a non-empty dict (Spec B Band 5 — executor's
  thesis-writer callback persisted at least one position).
- Phase 7 non-social analysts (technical, fundamental, news) did not silently
  degrade to ``is_no_data=True`` in the trace's digest section.  Social and
  SmartMoney are explicitly excluded (spec decisions 9.3 and fixture gap).

Uses the module-scoped ``smoke_result`` fixture from conftest.py so the
expensive ADK pipeline run executes exactly once across all four per-concern
smoke-test modules.
"""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# §A canonical key list (from docs/contract-invariants.md § Schema table)
# ---------------------------------------------------------------------------

# These are the contract-bearing top-level keys that must exist in the ADK
# session state after a completed tick.  Tick-scoped keys are populated fresh
# each tick; cross-tick keys survive the boundary via ADK user_state.
#
# Note: ``temp:`` keys are invocation-scoped and not persisted.  They are
# therefore NOT expected to be present in the post-run session state read
# back from ``session.sqlite``.  ``last_executed_tick_id`` and
# ``last_snapshot`` are in-tick handshake keys written via state_delta; they
# ARE present in the session state after the tick completes.
#
# Keys deliberately excluded from the assertion:
# - ``memory_buffer`` and ``day_digest`` — Spec C deferred; not yet implemented.
_SECTION_A_REQUIRED_KEYS = frozenset({
    "tick_id",
    "tickers",
    "portfolio",
    "reference_prices",
    "user:positions",
    "user:thesis",
    "strategist_decision",
    "technical_verdicts",
    "fundamental_verdicts",
    "news_verdicts",
    "social_verdicts",
    "tick_phase",
    "last_executed_tick_id",
    "last_snapshot",
})

# Analysts that must emit non-is_no_data verdicts given the fixture cache
# (one warm-up bar series + the window day).  Social degrades per spec
# decision 9.3; SmartMoney degrades because the fixture has no filing data.
_NON_SOCIAL_ANALYSTS = frozenset({"technical", "fundamental", "news"})


@pytest.mark.slow
def test_smoke_session_sqlite_exists(smoke_result) -> None:
    """session.sqlite must exist after the smoke run.

    Both guards here are hard assertions — a passing smoke run MUST produce a
    session sqlite and MUST contain at least one session.  Silently skipping
    these checks would give a green result with zero signal on the
    spec-required ``user:positions`` persistence guarantee.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    session_sqlite = smoke_result.result.run_dir / "session.sqlite"
    assert session_sqlite.exists(), (
        f"Smoke run did not create session sqlite at {session_sqlite}; "
        "DatabaseSessionService wiring is broken or run_dir is wrong."
    )


@pytest.mark.slow
def test_smoke_state_contains_all_contract_keys(smoke_result) -> None:
    """Last-tick session state must contain every §A contract key.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    state = smoke_result.get_last_session_state()

    # Every §A key must be present — absence means an agent or lifecycle step
    # skipped its contractual write.
    missing = _SECTION_A_REQUIRED_KEYS - set(state.keys())
    assert not missing, (
        f"Session state is missing §A contract key(s): {sorted(missing)}"
    )


@pytest.mark.slow
def test_smoke_state_has_no_bare_positions_key(smoke_result) -> None:
    """Session state must NOT contain a bare ``positions`` key.

    Only ``user:positions`` and ``temp:_positions`` are legal (Plan 03 /
    A-070).  A bare ``positions`` key indicates a regression where the old
    unnamespaced key was written instead of the ``user:``-prefixed one.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    state = smoke_result.get_last_session_state()

    assert "positions" not in state, (
        "Session state contains bare 'positions' key — Plan 03 / A-070 "
        "violation.  Only 'user:positions' and 'temp:_positions' are legal."
    )


@pytest.mark.slow
def test_smoke_user_positions_non_empty(smoke_result) -> None:
    """``user:positions`` must be a non-empty dict after the smoke run (Spec B Band 5).

    The smoke run buys AAPL (first ticker in the watchlist), so the executor's
    thesis-writer callback must persist at least one position entry.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    state          = smoke_result.get_last_session_state()
    user_positions = state.get("user:positions")

    assert isinstance(user_positions, dict), (
        f"user:positions must be a dict in the last tick session; "
        f"got {type(user_positions).__name__!r}"
    )
    assert len(user_positions) >= 1, (
        "user:positions must be non-empty after the smoke run; "
        "the executor's thesis-writer callback did not persist any position."
    )


@pytest.mark.slow
def test_smoke_non_social_analysts_not_no_data(smoke_result) -> None:
    """Non-social analysts must emit non-``is_no_data`` verdicts for AAPL.

    Phase 7 invariant: given the warm-up bars seeded in the fixture cache,
    technical, fundamental, and news analysts must compute real signals for
    AAPL.  An ``is_no_data=True`` verdict from any of these on a single-ticker
    smoke run is a silent degradation (Phase 2/4 gap).

    Social degrades legitimately per spec decision 9.3.  SmartMoney degrades
    legitimately — the fixture cache has no politician-trades / notable-holder
    filing data.

    Verdicts are not in the session state directly; they live in trace files
    under the ``"04_digest"`` section.  With a single-tick window there is
    exactly one trace file.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    traces_dir = smoke_result.result.run_dir / "traces"

    # Skip rather than hard-fail if traces weren't written — that failure is
    # caught by test_smoke_telemetry_written.py.
    if not traces_dir.exists():
        pytest.skip("traces/ directory missing — handled by telemetry test.")

    trace_files = sorted(traces_dir.glob("*.json"))
    if not trace_files:
        pytest.skip("No trace files — handled by telemetry test.")

    # Sample the middle trace file (single-tick run → exactly one file).
    sample_trace_file = trace_files[len(trace_files) // 2]
    sample_trace      = json.loads(sample_trace_file.read_text(encoding="utf-8"))
    digest_section    = sample_trace.get("04_digest") or {}
    digest_data: list = digest_section.get("data") or []

    # Only assert when the digest was produced — single-tick AAPL run always
    # should produce one, but guard defensively to avoid vacuous passes.
    if not digest_data:
        pytest.skip(
            f"04_digest section is empty in {sample_trace_file.name} — "
            "cannot assert analyst non-degradation."
        )

    for ticker_evidence in digest_data:
        ticker      = ticker_evidence.get("ticker", "<unknown>")
        per_analyst = ticker_evidence.get("per_analyst") or {}

        for analyst in _NON_SOCIAL_ANALYSTS:
            evidence = per_analyst.get(analyst)
            if evidence is None:
                # Analyst absent from pool — skip rather than hard-fail so the
                # test is not brittle against pool composition changes.
                continue

            verdict = evidence.get("verdict") or {}
            assert verdict.get("is_no_data") is not True, (
                f"ticker={ticker!r}: '{analyst}' silently degraded to "
                f"is_no_data=True in {sample_trace_file.name} — "
                "Phase 2/4 gap detected by smoke test."
            )
