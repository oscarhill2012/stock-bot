"""Smoke concern 2 — telemetry and observability artefacts are written.

Asserts:
- ``traces/`` directory exists and contains at least one ``.json`` trace file.
- ``report/equity_curve.png`` exists.
- ``report/metrics.md`` exists, contains the expected section header, and the
  total-return metric is a valid percentage (not NaN).
- ``manifest.json`` has ``audit_complete=True``.
- ``audit/`` directory exists and contains at least one ``.tick.json`` record.
- No definitive-leak tripwire (``wall_clock_fallback_fired``,
  ``any_filter_key_after_as_of``, ``missing_timestamp_rows_seen``) is fired in
  any audit record.

Uses the module-scoped ``smoke_result`` fixture from conftest.py so the
expensive ADK pipeline run executes exactly once across all four per-concern
smoke-test modules.
"""
from __future__ import annotations

import json
import re

import pytest


# Tripwires that indicate a definitive point-in-time data leak.
# Advisory tripwires (``*_advisory`` suffix) are intentionally excluded:
# - ``open_tick_sameday_bar_advisory``: the store's inclusive-range query
#   surfaces the same-day bar at the raw read level, but
#   ``price_history_cache.fetch`` strips it before any analyst receives it.
# - ``midnight_utc_timestamps_seen_advisory``: date-only sources promote all
#   timestamps to midnight UTC — steady-state behaviour, not a leak.
DEFINITIVE_LEAK_TRIPWIRES = frozenset({
    "wall_clock_fallback_fired",
    "any_filter_key_after_as_of",
    "missing_timestamp_rows_seen",
})


@pytest.mark.slow
def test_smoke_trace_files_written(smoke_result) -> None:
    """Traces directory must exist and contain at least one tick trace file.

    The backtest driver writes one JSON trace file per tick into
    ``<run_dir>/traces/``.  A missing traces directory or empty file list
    means the trace writer is broken or un-wired.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    traces_dir = smoke_result.result.run_dir / "traces"
    assert traces_dir.exists(), "traces/ directory not created"

    trace_files = list(traces_dir.glob("*.json"))
    assert len(trace_files) >= 1, (
        "No trace files written to traces/ — TraceWriter is un-wired or broken."
    )


@pytest.mark.slow
def test_smoke_equity_curve_written(smoke_result) -> None:
    """Equity curve PNG must be produced by the reporting module.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    equity_curve = smoke_result.result.run_dir / "report" / "equity_curve.png"
    assert equity_curve.exists(), (
        "report/equity_curve.png not produced — reporting module is broken."
    )


@pytest.mark.slow
def test_smoke_metrics_md_written(smoke_result) -> None:
    """Metrics markdown must exist, have the expected header, and not contain NaN.

    The total-return metric must be a valid percentage.  Sharpe may be NaN in a
    short zero-variance single-tick run — that is acceptable and expected.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    metrics_path = smoke_result.result.run_dir / "report" / "metrics.md"
    assert metrics_path.exists(), "report/metrics.md not produced"

    metrics_text = metrics_path.read_text(encoding="utf-8")

    # File must be non-empty and contain the canonical section header.
    assert "# Backtest metrics" in metrics_text, (
        f"metrics.md missing header:\n{metrics_text}"
    )

    # Total return must be present and must be a valid percentage (not NaN).
    total_return_match = re.search(r"Total return.*\*\*([^*]+)\*\*", metrics_text)
    assert total_return_match is not None, (
        f"metrics.md missing Total return line:\n{metrics_text}"
    )
    assert "nan" not in total_return_match.group(1).lower(), (
        f"Total return is NaN in metrics.md:\n{metrics_text}"
    )


@pytest.mark.slow
def test_smoke_audit_records_written(smoke_result) -> None:
    """Audit directory must exist, contain tick records, and have no definitive leaks.

    §5.4 — every scheduled tick must produce a ``.tick.json`` telemetry record,
    and ``manifest.audit_complete`` must be ``True``.  Each record's definitive
    tripwires must all be ``False`` — a fired tripwire indicates a potential
    point-in-time data leak.

    Parameters
    ----------
    smoke_result:
        Module-scoped bundle from conftest.py containing the ``RunResult``.
    """
    result = smoke_result.result

    # Manifest must declare audit_complete=True (all scheduled ticks wrote a
    # telemetry record).
    manifest = json.loads((result.run_dir / "manifest.json").read_text())
    assert manifest.get("audit_complete") is True, (
        f"manifest.audit_complete is not True: {manifest.get('audit_complete')!r}"
    )

    # audit/ directory and at least one record.
    audit_dir = result.run_dir / "audit"
    assert audit_dir.exists(), "audit/ directory not created"

    audit_files = list(audit_dir.glob("*.tick.json"))
    assert len(audit_files) >= 1, (
        "No audit telemetry records written — AuditingStore is un-wired or broken."
    )

    # No definitive-leak tripwire must be fired in any audit record.
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
