# tests/unit/test_scheduler_yaml.py
from __future__ import annotations

from pathlib import Path

import yaml


SCHED = Path(__file__).resolve().parents[2] / "deploy" / "scheduler.yaml"


def test_yaml_parses():
    data = yaml.safe_load(SCHED.read_text())
    assert isinstance(data, dict)


def test_cron_is_market_hours_weekdays():
    data = yaml.safe_load(SCHED.read_text())
    schedule = data.get("schedule", "")
    # Phase 1 design: 30 9-15 * * 1-5 America/New_York
    assert "9-15" in schedule
    assert "1-5" in schedule
    assert data.get("timeZone") == "America/New_York"


def test_targets_run_job():
    data = yaml.safe_load(SCHED.read_text())
    target = data.get("httpTarget", {})
    assert "uri" in target
    assert "stockbot-tick" in target["uri"]
