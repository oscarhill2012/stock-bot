"""Unit tests for the schedule.json config loader.

Covers:
- The live ``config/schedule.json`` parses successfully.
- Every ``tick_times_et`` entry is a valid 24-hour ``HH:MM`` string.
- The list length equals ``ticks_per_day``.
- The loader rejects malformed time strings.
- The loader rejects a mismatch between ``ticks_per_day`` and the list length.
- The ``lru_cache`` singleton clears correctly between tests (handled by the
  autouse fixture in ``tests/conftest.py`` — schedule cache is cleared here).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.schedule import ScheduleConfig, get_schedule_config, load_schedule_config

# Regex matching a valid 24-hour HH:MM string — mirrors the pattern in the
# production loader; duplicated here so the test is self-documenting.
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


# ---------------------------------------------------------------------------
# Autouse fixture — clear get_schedule_config lru_cache between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_schedule_config_cache():
    """Ensure each test starts with a fresh ScheduleConfig singleton.

    Mirrors the ``_clear_analysts_config_cache`` fixture in
    ``tests/conftest.py``.
    """
    get_schedule_config.cache_clear()
    yield
    get_schedule_config.cache_clear()


# ---------------------------------------------------------------------------
# Live config sanity checks
# ---------------------------------------------------------------------------

def test_live_config_parses() -> None:
    """The real ``config/schedule.json`` must parse without errors.

    Verifies that the committed file is structurally sound — acts as a
    guard against accidental breakage of the source-of-truth config.
    """
    cfg = load_schedule_config()
    assert isinstance(cfg, ScheduleConfig)


def test_live_config_tick_times_are_valid_hhmm() -> None:
    """Every entry in ``tick_times_et`` from the live config is a valid HH:MM string."""
    cfg = load_schedule_config()
    for t in cfg.tick_times_et:
        assert _TIME_RE.match(t), (
            f"tick time {t!r} does not match HH:MM 24-hour format"
        )


def test_live_config_list_length_matches_ticks_per_day() -> None:
    """The live config's ``tick_times_et`` list length matches ``ticks_per_day``."""
    cfg = load_schedule_config()
    assert len(cfg.tick_times_et) == cfg.ticks_per_day, (
        f"tick_times_et has {len(cfg.tick_times_et)} entries "
        f"but ticks_per_day is {cfg.ticks_per_day}"
    )


def test_live_config_has_expected_tick_times() -> None:
    """The live config contains the two expected ET tick windows.

    09:45 ET (~15 min after NYSE open) and 16:30 ET (~30 min after close)
    are the agreed Phase 5 cadence. This test pins the actual values so that
    any inadvertent change surfaces immediately.
    """
    cfg = load_schedule_config()
    assert "09:45" in cfg.tick_times_et
    assert "16:30" in cfg.tick_times_et


# ---------------------------------------------------------------------------
# Custom-path tests (supply temp files to avoid touching the source tree)
# ---------------------------------------------------------------------------

def test_load_valid_custom_config(tmp_path: Path) -> None:
    """A well-formed custom config file loads correctly."""
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 2,
        "tick_times_et": ["09:45", "16:30"],
        "comment": "test config",
    }), encoding="utf-8")

    cfg = load_schedule_config(path=cfg_file)
    assert cfg.ticks_per_day == 2
    assert cfg.tick_times_et == ["09:45", "16:30"]
    assert cfg.comment == "test config"


def test_load_rejects_invalid_time_format(tmp_path: Path) -> None:
    """A time string that is not HH:MM (e.g. a plain integer) must fail validation."""
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 1,
        "tick_times_et": ["9:45"],  # Missing leading zero — not HH:MM
    }), encoding="utf-8")

    with pytest.raises(ValidationError, match="invalid tick time"):
        load_schedule_config(path=cfg_file)


def test_load_rejects_out_of_range_hours(tmp_path: Path) -> None:
    """Hours outside 00–23 must be rejected."""
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 1,
        "tick_times_et": ["25:00"],
    }), encoding="utf-8")

    with pytest.raises(ValidationError, match="invalid tick time"):
        load_schedule_config(path=cfg_file)


def test_load_rejects_out_of_range_minutes(tmp_path: Path) -> None:
    """Minutes outside 00–59 must be rejected."""
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 1,
        "tick_times_et": ["09:60"],
    }), encoding="utf-8")

    with pytest.raises(ValidationError, match="invalid tick time"):
        load_schedule_config(path=cfg_file)


def test_load_rejects_length_mismatch(tmp_path: Path) -> None:
    """A ``tick_times_et`` list with the wrong number of entries must fail.

    Here ``ticks_per_day`` is 3 but only 2 times are given.
    """
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 3,
        "tick_times_et": ["09:45", "16:30"],
    }), encoding="utf-8")

    with pytest.raises(ValidationError, match="must match"):
        load_schedule_config(path=cfg_file)


def test_load_rejects_empty_tick_times(tmp_path: Path) -> None:
    """An empty ``tick_times_et`` list must fail validation (min_length=1)."""
    cfg_file = tmp_path / "schedule.json"
    cfg_file.write_text(json.dumps({
        "ticks_per_day": 0,  # Also out-of-range (ge=1), triggers first
        "tick_times_et": [],
    }), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_schedule_config(path=cfg_file)


def test_lru_cache_clears_between_tests(tmp_path: Path) -> None:
    """The ``get_schedule_config`` singleton respects cache clears.

    Creates a minimal valid config, wires ``get_schedule_config`` to read it
    (via ``load_schedule_config`` path override is not available on the cached
    function, so this test just confirms the cache is fresh after the autouse
    fixture clears it — checking the function is callable and returns a
    ScheduleConfig from the real file).
    """
    # After _clear_schedule_config_cache cleared the cache, calling
    # get_schedule_config() must return a fresh load from the default path.
    cfg = get_schedule_config()
    assert isinstance(cfg, ScheduleConfig)

    # A second call returns the same object (cache hit).
    cfg2 = get_schedule_config()
    assert cfg is cfg2
