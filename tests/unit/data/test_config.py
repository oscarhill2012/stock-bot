"""Unit tests for data.config — DataConfig pydantic loader.

Phase 5 data-model split: ``stats`` domain retired; replaced by
``price_history`` and ``company_ratios`` in the providers config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data.config import DataConfig, FetchDefaults, load_config_from

VALID_PAYLOAD: dict = {
    "providers": {
        "price_history": "yfinance",
        "company_ratios": "yfinance",
        "news": "finnhub",
        "social_sentiment": "finnhub",
        "insider_trades": "edgar",
        "politician_trades": "quiver",
        "notable_holders": "edgar",
        "filings": "edgar",
        # Phase 3 (Task 3.0) — four new domains; provider names are placeholders
        # until the real modules land in Tasks 3.1, 3.3, 3.6, 3.7.
        "earnings": "finnhub",
        "analyst_consensus": "yfinance",
        "short_interest": "finra",
        "options": "yfinance",
    },
    "defaults": {
        "news_lookback_days": 7,
        "insider_lookback_days": 30,
        "politician_lookback_days": 90,
        "notable_holder_lookback_days": 180,
        "notable_holder_limit": 20,
        "history_period": "1y",
        "history_interval": "1d",
        "filings_per_form": 3,
        "include_filing_excerpts": True,
    },
    "quiver_http_timeout_seconds": 15.0,
}


def _write(tmp_path: Path, payload: dict) -> Path:
    """Write a JSON payload to a temp file and return the path."""
    p = tmp_path / "data.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_valid_config_loads(tmp_path: Path) -> None:
    """A fully-valid providers dict is accepted and parsed correctly."""
    cfg = load_config_from(_write(tmp_path, VALID_PAYLOAD))
    assert isinstance(cfg, DataConfig)
    assert cfg.providers["news"] == "finnhub"
    assert cfg.providers["price_history"] == "yfinance"
    assert cfg.providers["company_ratios"] == "yfinance"
    assert isinstance(cfg.defaults, FetchDefaults)
    assert cfg.defaults.news_lookback_days == 7
    assert cfg.quiver_http_timeout_seconds == 15.0


def test_unknown_domain_rejected(tmp_path: Path) -> None:
    """A provider config that includes an unknown domain raises ValidationError."""
    bad = {**VALID_PAYLOAD, "providers": {**VALID_PAYLOAD["providers"], "weather": "noaa"}}
    with pytest.raises(ValidationError, match="unknown domain"):
        load_config_from(_write(tmp_path, bad))


def test_missing_domain_rejected(tmp_path: Path) -> None:
    """A provider config that omits a required domain raises ValidationError."""
    incomplete = {
        **VALID_PAYLOAD,
        "providers": {k: v for k, v in VALID_PAYLOAD["providers"].items() if k != "news"},
    }
    with pytest.raises(ValidationError, match="missing"):
        load_config_from(_write(tmp_path, incomplete))


def test_malformed_json_raises(tmp_path: Path) -> None:
    """A non-JSON file raises JSONDecodeError."""
    p = tmp_path / "data.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_config_from(p)


def test_fetch_defaults_includes_earnings_and_short_interest(tmp_path: Path) -> None:
    """FetchDefaults exposes earnings_lookback_quarters and short_interest_lookback_days."""
    payload = {
        "providers": {
            "price_history":     "yfinance",
            "company_ratios":    "pit_composite",
            "news":              "alpha_vantage",
            "social_sentiment":  "finnhub",
            "insider_trades":    "edgar",
            "politician_trades": "fmp",
            "notable_holders":   "edgar",
            "filings":           "edgar",
            "earnings":          "finnhub",
            "analyst_consensus": "yfinance",
            "short_interest":    "finra",
            "options":           "yfinance",
        },
        "defaults": {
            "news_lookback_days":           7,
            "insider_lookback_days":        30,
            "politician_lookback_days":     90,
            "notable_holder_lookback_days": 180,
            "notable_holder_limit":         20,
            "history_period":               "1y",
            "history_interval":             "1d",
            "filings_per_form":             3,
            "include_filing_excerpts":      True,
            "earnings_lookback_quarters":   4,
            "short_interest_lookback_days": 90,
        },
        "quiver_http_timeout_seconds": 15.0,
    }
    path = tmp_path / "data.json"
    path.write_text(__import__("json").dumps(payload), encoding="utf-8")

    cfg = load_config_from(path)
    assert isinstance(cfg.defaults, FetchDefaults)
    assert cfg.defaults.earnings_lookback_quarters   == 4
    assert cfg.defaults.short_interest_lookback_days == 90
