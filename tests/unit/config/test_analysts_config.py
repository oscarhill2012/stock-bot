"""Unit tests for the analysts.json config loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from config.analysts import AnalystsConfig, load_analysts_config


def test_load_analysts_config_default_values(tmp_path: Path) -> None:
    """A minimal config file populates fields with the documented defaults."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 20, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    cfg = load_analysts_config(path=cfg_file)
    assert isinstance(cfg, AnalystsConfig)
    assert cfg.news.max_articles_per_ticker == 20
    assert cfg.fundamental.max_filing_mda_chars == 1500
    assert cfg.cache.enabled is True
    assert cfg.cache.directory == "cache/reports"


def test_load_analysts_config_rejects_negative_caps(tmp_path: Path) -> None:
    """Negative truncation caps must fail validation — they are sentinel-poisoning."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": -1, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)


def test_load_analysts_config_rejects_oversized_caps(tmp_path: Path) -> None:
    """An operator-error oversized cap (e.g. max_articles_per_ticker=9999) must
    fail validation — the upper bound exists for a reason."""
    cfg_file = tmp_path / "analysts.json"
    cfg_file.write_text(json.dumps({
        "news": {"max_articles_per_ticker": 9999, "max_summary_chars": 500},
        "fundamental": {
            "max_filing_mda_chars": 1500,
            "max_filing_risk_chars": 1500,
            "max_insider_footnotes": 5,
            "max_insider_footnote_chars": 400,
        },
        "cache": {"enabled": True, "directory": "cache/reports"},
    }))

    with pytest.raises(ValidationError):
        load_analysts_config(path=cfg_file)
