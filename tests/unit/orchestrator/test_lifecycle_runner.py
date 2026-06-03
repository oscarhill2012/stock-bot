# tests/unit/orchestrator/test_lifecycle_runner.py
"""Unit tests for the shared lifecycle runner helpers.

These cover the pure helpers (``iso_coerce_state``, ``build_seed_state``)
that both ``orchestrator.tick`` and ``backtest.driver`` rely on for
seed-state preparation.  ``build_runner`` itself is covered by the
integration parity test in ``tests/integration/test_lifecycle_parity.py``.
"""
from __future__ import annotations

from datetime import UTC, datetime

from orchestrator.lifecycle_runner import build_seed_state, iso_coerce_state


def test_iso_coerce_state_converts_datetime_to_iso_string() -> None:
    """A timezone-aware ``datetime`` becomes its ISO-8601 string form."""

    dt = datetime(2026, 5, 26, 14, 30, tzinfo=UTC)
    out = iso_coerce_state({"as_of": dt, "tick_id": "tick-001"})

    assert out["as_of"] == dt.isoformat()
    assert out["tick_id"] == "tick-001"


def test_iso_coerce_state_leaves_non_datetime_values_untouched() -> None:
    """Strings, ints, lists, and dicts must pass through unchanged."""

    payload = {
        "tickers":     ["AAPL", "MSFT"],
        "portfolio":   {"cash": 1000.0, "positions": {}},
        "tick_phase":  "live",
        "as_of":       "2026-05-26T14:30:00+00:00",  # already-string passthrough
    }

    out = iso_coerce_state(payload)

    assert out == payload


def test_build_seed_state_strips_temp_prefixed_keys() -> None:
    """``temp:``-prefixed keys must not survive into ``create_session``."""

    payload = {
        "tick_id":              "tick-002",
        "as_of":                datetime(2026, 5, 26, tzinfo=UTC),
        "temp:_trace":          object(),     # observability handle
        "temp:_obs_news_call":  {"foo": 1},   # observability scratch
    }

    out = build_seed_state(payload)

    assert "tick_id" in out
    assert "as_of"   in out
    assert isinstance(out["as_of"], str), "as_of must be ISO-coerced en route"
    assert all(not k.startswith("temp:") for k in out), (
        f"temp: keys leaked into seed state: {[k for k in out if k.startswith('temp:')]}"
    )

    # Pin the exact surviving key set — a regression that silently dropped
    # legitimate (non-temp:) keys must fail loudly, not pass quietly.
    assert set(out) == {"tick_id", "as_of"}
