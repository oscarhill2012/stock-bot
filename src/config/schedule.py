"""Loader for ``config/schedule.json`` — tick cadence + ET-keyed tick times.

A Pydantic-validated wrapper around the JSON file at the project root. The
module-level singleton ``get_schedule_config()`` is the production entry
point; ``load_schedule_config(path=...)`` exists for tests that want to feed
a custom file.

Tick times are stored as plain ``HH:MM`` strings in Eastern Time (ET /
``America/New_York``). The runner is responsible for converting them to UTC
at scheduling time using ``zoneinfo.ZoneInfo("America/New_York")``, which
means DST transitions (EDT ↔ EST) are handled automatically by the OS tz
database — no manual UTC offsets are needed here.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

# Project-root-relative default path. The package is imported via
# PYTHONPATH=src, so we resolve relative to the working directory rather
# than relative to this file.
_DEFAULT_PATH = Path("config/schedule.json")

# Regex matching a valid 24-hour HH:MM string (00:00 – 23:59).
_TIME_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


class ScheduleConfig(BaseModel):
    """Top-level shape of ``config/schedule.json``.

    Attributes
    ----------
    ticks_per_day:
        Expected number of ticks per trading day. Must match the length of
        ``tick_times_et`` — validated at load time.
    tick_times_et:
        Ordered list of ``HH:MM`` tick times in Eastern Time
        (``America/New_York``). The runner converts each to UTC via
        ``zoneinfo.ZoneInfo`` so that EDT/EST transitions are handled
        automatically. Must be non-empty.
    comment:
        Free-text annotation embedded in the JSON for operator guidance.
        Not used at runtime.
    """

    ticks_per_day: int = Field(ge=1, le=10)
    tick_times_et: list[str] = Field(min_length=1)
    comment:       str = ""

    @field_validator("tick_times_et", mode="after")
    @classmethod
    def _validate_time_strings(cls, times: list[str]) -> list[str]:
        """Ensure every entry is a valid 24-hour HH:MM string.

        Parameters
        ----------
        times:
            The raw list of time strings from the JSON.

        Returns
        -------
        list[str]
            The same list, unchanged, if all entries pass.

        Raises
        ------
        ValueError
            If any entry is not a valid 24-hour HH:MM string.
        """
        for t in times:
            if not _TIME_RE.match(t):
                raise ValueError(
                    f"invalid tick time {t!r}: expected HH:MM in 24-hour format"
                )
        return times

    @field_validator("tick_times_et", mode="after")
    @classmethod
    def _validate_length_matches_ticks_per_day(cls, times: list[str], info) -> list[str]:
        """Ensure ``tick_times_et`` has exactly ``ticks_per_day`` entries.

        Pydantic v2 validators receive the partially-constructed model data in
        ``info.data``; ``ticks_per_day`` may not be present if it failed its
        own validation, so we guard with ``.get()``.

        Parameters
        ----------
        times:
            The validated list of time strings.
        info:
            Pydantic v2 ``FieldValidationInfo`` — provides access to the
            sibling fields via ``info.data``.

        Returns
        -------
        list[str]
            The same list, unchanged, if the length matches.

        Raises
        ------
        ValueError
            If the list length differs from ``ticks_per_day``.
        """
        ticks_per_day = info.data.get("ticks_per_day")
        if ticks_per_day is not None and len(times) != ticks_per_day:
            raise ValueError(
                f"tick_times_et has {len(times)} entries but "
                f"ticks_per_day is {ticks_per_day}; they must match"
            )
        return times


def load_schedule_config(*, path: Path | None = None) -> ScheduleConfig:
    """Read and validate ``config/schedule.json``.

    Parameters
    ----------
    path:
        Override the default path. Useful in tests that want to supply a
        temporary file without touching the source tree.

    Returns
    -------
    ScheduleConfig
        Validated configuration object.

    Raises
    ------
    FileNotFoundError
        If the JSON file does not exist at the resolved path.
    json.JSONDecodeError
        If the file content is not valid JSON.
    pydantic.ValidationError
        If the parsed payload fails schema validation.
    """
    p = path or _DEFAULT_PATH
    payload = json.loads(p.read_text(encoding="utf-8"))
    return ScheduleConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_schedule_config() -> ScheduleConfig:
    """Production entry point — cached load of the default config path.

    The result is memoised via ``lru_cache`` so the JSON file is only read
    once per process. A process restart is required after editing
    ``config/schedule.json`` to pick up changes.

    Returns
    -------
    ScheduleConfig
        Validated configuration singleton.
    """
    return load_schedule_config()
