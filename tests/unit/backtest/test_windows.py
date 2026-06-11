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
        '{"start": "2023-03-06", "end": "2023-04-07", "notes": "test",'
        ' "risk_free_rate_annual": 0.048}}'
    )

    windows = load_windows(cfg)

    assert set(windows) == {"svb-stress-2023-03"}
    w = windows["svb-stress-2023-03"]
    assert isinstance(w, Window)
    assert w.start == date(2023, 3, 6)
    assert w.end   == date(2023, 4, 7)
    assert w.notes == "test"
    assert w.risk_free_rate_annual == pytest.approx(0.048)


def test_load_windows_rejects_inverted_range(tmp_path: Path) -> None:
    """end < start must raise a ValidationError (range check fires after field validation)."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "2023-04-07", "end": "2023-03-06", "notes": "",'
        ' "risk_free_rate_annual": 0.04}}'
    )

    with pytest.raises(Exception, match="end .* before start"):  # noqa: B017 — pydantic wraps ValueError in ValidationError
        load_windows(cfg)


def test_load_windows_rejects_malformed_date(tmp_path: Path) -> None:
    """Non-ISO date strings raise pydantic ValidationError."""
    cfg = tmp_path / "windows.json"
    cfg.write_text(
        '{"bad": {"start": "not-a-date", "end": "2023-04-07", "notes": "",'
        ' "risk_free_rate_annual": 0.04}}'
    )

    with pytest.raises(Exception):  # noqa: B017 — pydantic wraps the inner error in ValidationError
        load_windows(cfg)
