"""Unit tests for the analysts.json config loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.analysts import AnalystsConfig, load_analysts_config

# ---------------------------------------------------------------------------
# Shared minimal-config payload
# ---------------------------------------------------------------------------
# Each test below either uses this payload as-is or overrides one field.  The
# ``output_caps`` block is required by the schema (no defaults at the field
# level), so every fixture must carry it.
# ---------------------------------------------------------------------------

_MINIMAL_LLM_CAPS: dict = {
    "timeout_seconds":   60,
    "max_output_tokens": 2000,
    "timeout_retries":   3,
    "schema_retries":    3,
}

_MINIMAL_CFG: dict = {
    "news": {
        "max_articles_per_ticker": 20,
        "max_summary_chars":       500,
        "llm":                     _MINIMAL_LLM_CAPS,
    },
    "fundamental": {
        "max_filing_mda_chars":       1500,
        "max_filing_risk_chars":      1500,
        "max_insider_footnotes":         5,
        "max_insider_footnote_chars":  400,
        "llm":                         _MINIMAL_LLM_CAPS,
    },
    "output_caps": {
        "verdict_rationale_max_chars":   160,
        "report_summary_max_chars":     2000,
        "report_driver_name_max_chars":   60,
        "report_driver_body_max_chars": 1000,
    },
    "cache": {"enabled": True, "directory": "cache/reports"},
}


def test_load_analysts_config_default_values(tmp_path: Path) -> None:
    """A minimal config file populates fields with the documented defaults."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(_MINIMAL_CFG))

    cfg = load_analysts_config(path=cfg_file)
    assert isinstance(cfg, AnalystsConfig)
    assert cfg.news.max_articles_per_ticker == 20
    assert cfg.fundamental.max_filing_mda_chars == 1500
    assert cfg.cache.enabled is True
    assert cfg.cache.directory == "cache/reports"
    # slack_percent defaults to 10 when omitted from the JSON file.
    assert cfg.slack_percent == 10
    assert cfg.output_caps.verdict_rationale_max_chars == 160


def test_load_analysts_config_rejects_negative_caps(tmp_path: Path) -> None:
    """Negative truncation caps must fail validation — they are sentinel-poisoning."""
    payload = {
        **_MINIMAL_CFG,
        "news": {"max_articles_per_ticker": -1, "max_summary_chars": 500, "llm": _MINIMAL_LLM_CAPS},
    }
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_rejects_oversized_caps(tmp_path: Path) -> None:
    """An operator-error oversized cap (e.g. max_articles_per_ticker=9999) must
    fail validation — the upper bound exists for a reason."""
    payload = {
        **_MINIMAL_CFG,
        "news": {"max_articles_per_ticker": 9999, "max_summary_chars": 500, "llm": _MINIMAL_LLM_CAPS},
    }
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_schema_cap_applies_slack_percent(tmp_path: Path) -> None:
    """``AnalystsConfig.schema_cap`` should add ``slack_percent`` headroom using
    integer math.  Verifies both a value that floats cleanly (600 → 660) and
    one that doesn't (200 → 220) to pin the FP-rounding fix in place.
    """
    payload = {**_MINIMAL_CFG, "slack_percent": 10}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    cfg = load_analysts_config(path=cfg_file)
    assert cfg.schema_cap(200) == 220      # 200 * 1.10 — FP would give 220.00000…003
    assert cfg.schema_cap(600) == 660      # 600 * 1.10 — exact in FP
    assert cfg.schema_cap(160) == 176      # 160 * 1.10


def test_schema_cap_with_zero_slack_is_identity(tmp_path: Path) -> None:
    """With ``slack_percent=0`` the schema cap should equal the prompt cap."""
    payload = {**_MINIMAL_CFG, "slack_percent": 0}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    cfg = load_analysts_config(path=cfg_file)
    assert cfg.schema_cap(200) == 200
    assert cfg.schema_cap(1000) == 1000


def test_load_analysts_config_rejects_slack_percent_out_of_range(tmp_path: Path) -> None:
    """``slack_percent`` is bounded ``[0, 50]`` — a 99% override is operator
    error, not a tuning knob."""
    payload = {**_MINIMAL_CFG, "slack_percent": 99}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_exposes_news_llm_caps(tmp_path) -> None:
    """The loaded config exposes `news.llm.{timeout_seconds, max_output_tokens, timeout_retries, schema_retries}`."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    cfg = load_analysts_config(path=cfg_path)

    assert cfg.news.llm.timeout_seconds   == 60
    assert cfg.news.llm.max_output_tokens == 2000
    assert cfg.news.llm.timeout_retries   == 3
    assert cfg.news.llm.schema_retries    == 3

    assert cfg.fundamental.llm.timeout_seconds   == 60
    assert cfg.fundamental.llm.max_output_tokens == 2000
    assert cfg.fundamental.llm.timeout_retries   == 3
    assert cfg.fundamental.llm.schema_retries    == 3


def test_load_analysts_config_rejects_zero_timeout_seconds(tmp_path) -> None:
    """`timeout_seconds <= 0` raises at load time, not at first use."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   0,                       # invalid
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)


def test_load_analysts_config_rejects_tiny_max_output_tokens(tmp_path) -> None:
    """`max_output_tokens < 256` raises at load time."""

    cfg_path = tmp_path / "analysts.json"
    cfg_path.write_text(json.dumps({
        "slack_percent": 15,
        "news": {
            "max_articles_per_ticker": 25,
            "max_summary_chars":       1500,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 100,                     # below ge=256 floor
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "fundamental": {
            "max_filing_mda_chars":       1500,
            "max_filing_risk_chars":      1500,
            "max_insider_footnotes":      5,
            "max_insider_footnote_chars": 400,
            "llm": {
                "timeout_seconds":   60,
                "max_output_tokens": 2000,
                "timeout_retries":   3,
                "schema_retries":    3,
            },
        },
        "output_caps": {
            "verdict_rationale_max_chars":            200,
            "verdict_rationale_prompt_headroom_chars": 50,
            "report_summary_max_chars":     1000,
            "report_driver_name_max_chars":   60,
            "report_driver_body_max_chars": 500,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_path)
