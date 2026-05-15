"""Tests for the era-window config loader."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from backtest.windows import Window, load_windows


def test_load_windows_parses_svb_fixture(tmp_path: Path) -> None:
    """Canonical fixture parses into a dict[str, Window]."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"svb-stress-2023-03": '
        '{"start": "2023-03-06", "end": "2023-04-07", "notes": "test"}}'
    )

    windows = load_windows(cfg)

    assert set(windows) == {"svb-stress-2023-03"}
    w = windows["svb-stress-2023-03"]
    assert isinstance(w, Window)
    assert w.start == date(2023, 3, 6)
    assert w.end   == date(2023, 4, 7)
    assert w.notes == "test"


def test_load_windows_rejects_inverted_range(tmp_path: Path) -> None:
    """end < start must raise."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "2023-04-07", "end": "2023-03-06", "notes": ""}}'
    )

    with pytest.raises(ValueError, match="end .* before start"):
        load_windows(cfg)


def test_load_windows_rejects_malformed_date(tmp_path: Path) -> None:
    """Non-ISO date strings raise pydantic ValidationError."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "not-a-date", "end": "2023-04-07", "notes": ""}}'
    )

    with pytest.raises(Exception):  # pydantic ValidationError
        load_windows(cfg)
