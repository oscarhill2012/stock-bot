"""Smoke concern 3 — DecisionLogger writes JSON snapshot files.

Asserts:
- The ``decisions/`` directory exists under ``run_dir``.
- At least one ``.json`` snapshot file was written (one per filled order).
- Every snapshot file is valid JSON and carries the expected top-level keys.
- ``held_view_at_decision`` is a key present in every snapshot (it may be
  ``None`` for an opening buy where no prior position exists).

NOTE on ``held_view_at_decision`` and F-backtest-005
-----------------------------------------------------
The smoke run performs an opening buy of AAPL from a zero-position start.
``DecisionLogger._build_snapshot`` reads ``state["user:positions"].get(ticker)``
for ``held_view_at_decision``.  Because ``on_executions`` is called inside the
executor's ``_run_async_impl``, *before* the ``after_agent_callback`` writes
the new position into ``user:positions``, the prior held view for a brand-new
buy is ``None`` — by design (it captures the *prior* held state, not the new
one).

F-backtest-005 (verifying ``held_view_at_decision`` is populated) therefore
requires a test scenario that includes either:
  (a) a sell or update trade on a previously-held position, OR
  (b) a multi-tick smoke that opens on tick 1 and re-evaluates on tick 2.

Such a scenario is out of scope for the single-tick smoke run.  The
assertion here verifies the key is present (correctly ``None`` for an
opening buy) and that the full snapshot structure is intact.

Uses the module-scoped ``smoke_result`` fixture from conftest.py so the
expensive ADK pipeline run executes exactly once across all four per-concern
smoke-test modules.
"""
from __future__ import annotations

import json

import pytest

# Top-level keys that every decision snapshot must carry.
_REQUIRED_SNAPSHOT_KEYS = frozenset({
    "decision_id",
    "tick",
    "ticker",
    "side",
    "execution",
    "analyst_inputs",
    "analyst_outputs",
    "strategist_view",
    "strategist_decision",
})


@pytest.mark.slow
def test_smoke_decision_logger_writes_snapshots(smoke_result) -> None:
    """DecisionLogger must write at least one snapshot file for the buy trade.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    decisions_dir = smoke_result.result.run_dir / "decisions"
    assert decisions_dir.exists(), (
        "decisions/ directory not created — DecisionLogger is un-wired or broken."
    )

    snapshot_files = list(decisions_dir.glob("*.json"))
    assert len(snapshot_files) >= 1, (
        "No decision snapshot files written.  The smoke run should produce at "
        "least one filled order (AAPL buy); a missing file means DecisionLogger "
        "did not call on_executions, or all orders were rejected."
    )


@pytest.mark.slow
def test_smoke_decision_snapshots_have_required_keys(smoke_result) -> None:
    """Every decision snapshot must carry the full set of required top-level keys.

    This verifies that ``_build_snapshot`` ran to completion and serialised
    without error.  The ``strategist_view`` sub-object must contain a
    ``held_view_at_decision`` key (which may be ``None`` for an opening buy —
    see module docstring for why this is correct behaviour and the limitation
    it implies for F-backtest-005).

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    decisions_dir  = smoke_result.result.run_dir / "decisions"
    snapshot_files = list(decisions_dir.glob("*.json"))

    # Guard: skip if decisions/ is missing (caught by the previous test).
    if not decisions_dir.exists() or not snapshot_files:
        pytest.skip("No decision snapshot files to check (handled by previous test).")

    for snapshot_file in snapshot_files:
        snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))

        # All top-level keys must be present.
        missing = _REQUIRED_SNAPSHOT_KEYS - set(snapshot.keys())
        assert not missing, (
            f"Snapshot {snapshot_file.name} is missing required key(s): "
            f"{sorted(missing)}"
        )

        # strategist_view must have a held_view_at_decision key — even if its
        # value is None (opening buy with no prior position).
        strategist_view = snapshot.get("strategist_view") or {}
        assert "held_view_at_decision" in strategist_view, (
            f"strategist_view in {snapshot_file.name} is missing "
            "'held_view_at_decision' key — DecisionLogger._build_snapshot "
            "did not produce this field at all (different from it being None)."
        )
