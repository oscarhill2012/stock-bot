"""Era-window config loader for the backtest harness.

Reads ``config/backtest_windows.json`` and returns a dict of validated
``Window`` records keyed by the era slug (e.g. ``"svb-stress-2023-03"``).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class Window(BaseModel):
    """One historical era window — inclusive ``[start, end]`` date range."""

    start: date
    end:   date
    notes: str = ""

    # Window-average 3-month T-bill rate, sourced from FRED series DTB3.
    # Used by the reporting layer to compute excess returns for Sharpe and
    # to credit the cash fraction of the matched-exposure benchmark.
    risk_free_rate_annual: float = Field(
        ...,
        ge=0.0,
        le=0.2,
        description=(
            "Window-average 3-month T-bill yield, annualised, sourced from "
            "FRED series DTB3."
        ),
    )

    @model_validator(mode="after")
    def _check_range(self) -> Window:
        # Reject backwards ranges early; downstream tick schedule would silently
        # yield zero ticks otherwise, which is the worst kind of "nothing happens".
        if self.end < self.start:
            raise ValueError(f"end ({self.end}) before start ({self.start})")
        return self


def load_windows(path: Path) -> dict[str, Window]:
    """Load and validate every window definition in the JSON file at ``path``."""
    raw = json.loads(Path(path).read_text())
    return {key: Window.model_validate(value) for key, value in raw.items()}
