"""H4 — derived rationale prompt budget on AnalystsConfig.

The schema cap (``verdict_rationale_max_chars``) absorbs the LLM's natural
overshoot via ``slack_percent``.  The prompt-facing budget sits *below* the
schema cap so the LLM has room to overshoot without tripping schema
validation.  This module verifies the derived property's behaviour at the
four interesting points along the headroom axis.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.analysts import load_analysts_config


def _write_config(tmp_path: Path, *, cap: int, headroom: int) -> Path:
    """Build a minimal ``config/analysts.json``-shaped fixture file.

    Only the fields required by ``AnalystsConfig`` are populated; sensible
    defaults are used for every other knob so the loader does not fail
    validation on the surrounding shape.
    """
    payload = {
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 50,
            "max_summary_chars": 800,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2048,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       8000,
            "max_filing_risk_chars":      4000,
            "max_insider_footnotes":      10,
            "max_insider_footnote_chars": 800,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2048,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "cache": {"enabled": False, "directory": "/tmp/cache"},
        "output_caps": {
            "verdict_rationale_max_chars":              cap,
            "verdict_rationale_prompt_headroom_chars":  headroom,
            "report_summary_max_chars":                 2000,
            "report_driver_name_max_chars":             80,
            "report_driver_body_max_chars":             400,
        },
    }
    p = tmp_path / "analysts.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_default_headroom_subtracts(tmp_path: Path) -> None:
    """200 cap minus 50 headroom yields a 150-char prompt budget."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=50))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 150


def test_zero_headroom_returns_cap(tmp_path: Path) -> None:
    """Zero headroom — the prompt budget equals the schema cap."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=0))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 200


def test_negative_headroom_clamps_to_cap(tmp_path: Path) -> None:
    """Negative headroom would push the budget above the cap — clamp to cap."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=-50))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 200


def test_oversize_headroom_clamps_to_floor(tmp_path: Path) -> None:
    """Headroom > cap would yield ≤0 — clamp to the 40-char floor."""
    cfg = load_analysts_config(path=_write_config(tmp_path, cap=200, headroom=500))
    assert cfg.output_caps.verdict_rationale_prompt_budget == 40
