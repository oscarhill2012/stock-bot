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

_MINIMAL_CFG: dict = {
    "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
    "fundamental": {
        "max_filing_mda_chars":       1500,
        "max_filing_risk_chars":      1500,
        "max_insider_footnotes":         5,
        "max_insider_footnote_chars":  400,
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
    payload = {**_MINIMAL_CFG, "news": {"max_articles_per_ticker": -1, "max_summary_chars": 500}}
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps(payload))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_rejects_oversized_caps(tmp_path: Path) -> None:
    """An operator-error oversized cap (e.g. max_articles_per_ticker=9999) must
    fail validation — the upper bound exists for a reason."""
    payload = {**_MINIMAL_CFG, "news": {"max_articles_per_ticker": 9999, "max_summary_chars": 500}}
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
