"""S3 — audit ``cache_hits`` count matches structured-log ``report_cache_hit`` count.

The previous state-mutation source dropped on ADK session merge.  Audit
now reads obs/logs/ directly; the two counts agree by construction.
"""
from __future__ import annotations

import json
from pathlib import Path


def _count_hits(audit_record: dict) -> int:
    """Return the audit-side report-cache-hit count for one tick."""
    return len(audit_record.get("report_cache_hits", []))


def _count_log_hits(log_payload: dict) -> int:
    """Return the structured-log ``report_cache_hit`` event count."""
    return sum(
        1 for event in log_payload.get("events", [])
        if event.get("message") == "report_cache_hit"
    )


def test_audit_cache_hits_match_log_count_for_known_tick(tmp_path: Path) -> None:
    """Audit and log counts agree on a hand-crafted tick."""

    log_payload = {
        "events": [
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_miss", "logger": "agents.analysts.cache_callbacks"},
            {"message": "report_cache_hit",  "logger": "agents.analysts.cache_callbacks"},
        ],
    }

    from backtest.audit.telemetry import build_telemetry_record_from_logs

    record = build_telemetry_record_from_logs(log_payload=log_payload)

    assert _count_hits(record) == _count_log_hits(log_payload)
