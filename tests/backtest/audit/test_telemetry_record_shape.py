"""``build_telemetry_record`` returns the agreed schema."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from backtest.audit.telemetry import build_telemetry_record, write_telemetry_record
from backtest.schedule import Tick


def _tick() -> Tick:
    return Tick(
        as_of=datetime(2023, 3, 10, 9, 30, tzinfo=UTC),
        phase="open",
    )


def test_record_has_expected_top_level_keys() -> None:
    """Record matches the schema documented in the spec §4.1."""
    record = build_telemetry_record(
        tick=_tick(),
        run_id="r1",
        strict_mode=True,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
    )

    expected = {
        "tick_id", "as_of", "phase", "strict_mode",
        "tripwires", "per_domain",
        "report_cache_hits", "db_writes_recorded_at",
    }
    assert expected == set(record.keys())

    expected_tripwires = {
        # Actionable keys.
        "wall_clock_fallback_fired",
        "any_filter_key_after_as_of",
        "missing_timestamp_rows_seen",
        # Advisory keys — benign by design; excluded from actionable counts.
        "open_tick_sameday_bar_advisory",
        "midnight_utc_timestamps_seen_advisory",
    }
    assert expected_tripwires == set(record["tripwires"].keys())


def test_writer_creates_one_file_per_tick(tmp_path: Path) -> None:
    """``write_telemetry_record`` writes ``<tick-slug>.tick.json``."""
    record = build_telemetry_record(
        tick=_tick(),
        run_id="r1",
        strict_mode=True,
        per_domain={},
        report_cache_hits=[],
        db_writes_recorded_at={},
    )

    path = write_telemetry_record(tmp_path, record)
    assert path.exists()
    assert path.name.endswith(".tick.json")
