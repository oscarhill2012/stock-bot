"""Unit tests for ``src/config/strategist.py`` — Pydantic-validated loader
for ``config/strategist.json``.

Focus of this file: the new ``llm`` block carrying the per-strategist
timeout / max-tokens / retry budgets.  Other strategist config (char caps,
slack) is covered by existing call-site tests.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from config.strategist import load_strategist_config


def _valid_strategist_json() -> dict:
    """Return a minimum-valid strategist.json payload as a dict.

    Used as the starting point for both happy-path and bad-value tests
    so each test only highlights the specific field it perturbs.

    Note: field names mirror the real ``DecisionCaps`` model (``thesis_max_chars``,
    not the plan's draft ``updated_thesis_max_chars``).
    """

    return {
        "slack_percent": 15,
        "decision_caps": {
            "reasoning_max_chars": 1000,
            "thesis_max_chars":     800,
        },
        "stance_caps": {
            "rationale_max_chars":    250,
            "catalyst_max_chars":     120,
            "close_reason_max_chars": 120,
            "trim_reason_max_chars":  120,
        },
        "position_thesis_caps": {
            "rationale_max_chars":          400,
            "catalyst_max_chars":           100,
            "last_review_note_max_chars":   200,
        },
        "llm": {
            "timeout_seconds":   180,
            "max_output_tokens": 8000,
            "timeout_retries":   3,
            "schema_retries":    3,
        },
    }


def test_load_strategist_config_exposes_llm_caps(tmp_path) -> None:
    """The loaded config exposes `strategist.llm.{...}` with correct values."""

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(_valid_strategist_json()))

    cfg = load_strategist_config(path=p)

    assert cfg.llm.timeout_seconds   == 180
    assert cfg.llm.max_output_tokens == 8000
    assert cfg.llm.timeout_retries   == 3
    assert cfg.llm.schema_retries    == 3


def test_load_strategist_config_rejects_zero_timeout(tmp_path) -> None:
    """`timeout_seconds <= 0` raises at load time."""

    payload = _valid_strategist_json()
    payload["llm"]["timeout_seconds"] = 0

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_strategist_config(path=p)


def test_load_strategist_config_rejects_tiny_max_output_tokens(tmp_path) -> None:
    """`max_output_tokens < 256` raises at load time."""

    payload = _valid_strategist_json()
    payload["llm"]["max_output_tokens"] = 100

    p = tmp_path / "strategist.json"
    p.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_strategist_config(path=p)


def test_load_strategist_config_rejects_zero_retries(tmp_path) -> None:
    """`timeout_retries < 1` and `schema_retries < 1` both raise."""

    payload_timeout = _valid_strategist_json()
    payload_timeout["llm"]["timeout_retries"] = 0
    p1 = tmp_path / "strategist1.json"
    p1.write_text(json.dumps(payload_timeout))
    with pytest.raises(ValidationError):
        load_strategist_config(path=p1)

    payload_schema = _valid_strategist_json()
    payload_schema["llm"]["schema_retries"] = 0
    p2 = tmp_path / "strategist2.json"
    p2.write_text(json.dumps(payload_schema))
    with pytest.raises(ValidationError):
        load_strategist_config(path=p2)
