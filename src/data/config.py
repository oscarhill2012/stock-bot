"""Typed loader for `config/data.json`.

The loader validates that `providers` covers exactly the seven known
domains. Cross-checking that each `(domain, provider_name)` is registered
happens at `data` package import time, after providers have been imported.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

# Mirrors data.registry.DOMAINS. Defined here too to avoid a circular
# import (config validates without needing the registry to exist yet).
_DOMAINS: frozenset[str] = frozenset({
    "stats",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
})


class FetchDefaults(BaseModel):
    news_lookback_days: int = 7
    insider_lookback_days: int = 30
    politician_lookback_days: int = 90
    notable_holder_lookback_days: int = 180
    notable_holder_limit: int = 20
    history_period: str = "1y"
    history_interval: str = "1d"
    filings_per_form: int = 3
    include_filing_excerpts: bool = True


class DataConfig(BaseModel):
    providers: dict[str, str]
    defaults: FetchDefaults = Field(default_factory=FetchDefaults)
    http_timeout_seconds: float = 15.0

    @model_validator(mode="after")
    def _check_domains(self) -> DataConfig:
        unknown = set(self.providers) - _DOMAINS
        if unknown:
            raise ValueError(f"unknown domain(s) in providers: {sorted(unknown)}")
        missing = _DOMAINS - set(self.providers)
        if missing:
            raise ValueError(f"missing provider(s) for domain(s): {sorted(missing)}")
        return self


_DEFAULT_PATH = Path("config/data.json")
_cache: DataConfig | None = None


def load_config_from(path: Path) -> DataConfig:
    """Load and validate `data.json` from a specific path. Used by tests."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return DataConfig.model_validate(payload)


def get_config() -> DataConfig:
    """Return the cached `DataConfig` (loaded from `config/data.json`)."""
    global _cache
    if _cache is None:
        _cache = load_config_from(_DEFAULT_PATH)
    return _cache


def _reset_cache() -> None:
    """Test-only: drop the cached config so `get_config()` reloads."""
    global _cache
    _cache = None
