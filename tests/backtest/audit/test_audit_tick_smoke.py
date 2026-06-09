"""``scripts.backtest_audit_tick`` produces a JSONL + SUMMARY.md."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest.audit.deep_dump import write_deep_dump


@pytest.mark.slow
def test_deep_dump_writes_files(tmp_path: Path) -> None:
    """Calling ``write_deep_dump`` with a captured-rows dict writes both files."""
    rows = [
        {
            "tick_as_of":         "2023-03-10T09:30:00-05:00",
            "analyst":            "news",
            "ticker":             "AAPL",
            "domain":             "news",
            "row_id":             "h:0",
            "filter_key_field":   "published_at",
            "filter_key_value":   "2023-03-09T12:00:00+00:00",
            "delta_to_as_of_sec": -77400,
            "upstream_evidence":  {
                "source":              "(no-verify)",
                "verification_status": "skip",
                "reason":              "no upstream verifier for this domain",
            },
            "fabricated_timestamp": False,
            "midnight_utc":         False,
            "same_day_as_as_of":    False,
        }
    ]

    full_path, summary_path = write_deep_dump(
        audit_dir=tmp_path,
        tick_slug="2023-03-10T09-30-00-05-00-open",
        rows=rows,
    )

    assert full_path.exists() and full_path.suffix == ".jsonl"
    assert summary_path.exists() and summary_path.suffix == ".md"

    parsed = json.loads(full_path.read_text().strip().splitlines()[0])
    assert parsed["analyst"] == "news"
    assert "Tripwire summary" in summary_path.read_text()
