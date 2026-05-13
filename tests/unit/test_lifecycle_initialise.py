"""Unit tests for lifecycle initialisation helpers.

Covers the `_check_heuristics()` boot-time validation hook added in Phase 5.
"""
from __future__ import annotations

import json

import pytest


def test_check_heuristics_raises_on_malformed_config(monkeypatch, tmp_path):
    """A malformed `analyst_heuristics.json` must surface at boot via initialise()."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    monkeypatch.setenv("ANALYST_HEURISTICS_PATH", str(bad))

    from agents.analysts.heuristics import load_heuristics
    from lifecycle.initialise import (
        _check_heuristics,  # imported here so module-time errors surface
    )

    load_heuristics.cache_clear()
    # Syntactically invalid JSON → JSONDecodeError. Schema-failure path is covered in tests/unit/test_analyst_heuristics.py.
    with pytest.raises(json.JSONDecodeError):
        _check_heuristics()
