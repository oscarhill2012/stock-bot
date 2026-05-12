"""Unit tests for data.config — DataConfig pydantic loader."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from data.config import DataConfig, FetchDefaults, load_config_from

VALID_PAYLOAD: dict = {
    "providers": {
        "stats": "yfinance",
        "news": "finnhub",
        "social_sentiment": "finnhub",
        "insider_trades": "edgar",
        "politician_trades": "quiver",
        "notable_holders": "edgar",
        "filings": "edgar",
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
    "http_timeout_seconds": 15.0,
}


def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "data.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_valid_config_loads(tmp_path: Path) -> None:
    cfg = load_config_from(_write(tmp_path, VALID_PAYLOAD))
    assert isinstance(cfg, DataConfig)
    assert cfg.providers["news"] == "finnhub"
    assert isinstance(cfg.defaults, FetchDefaults)
    assert cfg.defaults.news_lookback_days == 7
    assert cfg.http_timeout_seconds == 15.0


def test_unknown_domain_rejected(tmp_path: Path) -> None:
    bad = {**VALID_PAYLOAD, "providers": {**VALID_PAYLOAD["providers"], "weather": "noaa"}}
    with pytest.raises(ValidationError, match="unknown domain"):
        load_config_from(_write(tmp_path, bad))


def test_missing_domain_rejected(tmp_path: Path) -> None:
    incomplete = {**VALID_PAYLOAD, "providers": {k: v for k, v in VALID_PAYLOAD["providers"].items() if k != "news"}}
    with pytest.raises(ValidationError, match="missing"):
        load_config_from(_write(tmp_path, incomplete))


def test_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "data.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        load_config_from(p)
