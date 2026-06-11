"""Typed loader for `config/data.json`.

The loader validates that `providers` covers exactly the domains declared
in the canonical `data.domains.DOMAINS` frozenset. Cross-checking that each
`(domain, provider_name)` is registered happens at `data` package import
time, after providers have been imported.
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

# Re-export from the canonical leaf so ``DataConfig._check_domains`` and the
# registry validate against the same frozenset object — eliminating the old
# comment-enforced sync.  The underscore-prefixed alias is kept because the
# name is module-internal here; the public name lives in ``data.domains``.
from .domains import DOMAINS as _DOMAINS


class FetchDefaults(BaseModel):
    news_lookback_days:           int  = 7
    insider_lookback_days:        int  = 30
    politician_lookback_days:     int  = 90
    notable_holder_lookback_days: int  = 180
    notable_holder_limit:         int  = 20
    filings_per_form:             int  = 3
    include_filing_excerpts:      bool = True
    # Safety cap on the number of Form 4 filings listed per EDGAR query.
    # Purely a valve against pathological responses — it must be sized well
    # above any realistic window (an NVDA-class ticker files ~30/month, so a
    # 12-month backfill is ~400).  The old hardcoded ``head(50)`` silently
    # truncated long-window backfills (found in the 2026-06-11 cache audit).
    form4_max_filings:            int  = 1000
    # Lookback window honoured by the backtest filings cache provider when
    # serving ``get_company_filings``.  The live EDGAR provider derives its
    # own window from ``form_types`` + ``limit`` and ignores this value;
    # only the cache replay path consults it.
    filings_lookback_days:        int  = 90
    # 8-K visibility horizon for the shared analyst-visibility rule
    # (``data.filing_selection.select_current_filings``) — an 8-K older than
    # this many days is no longer analyst-visible.  Periodic forms (10-K /
    # 10-Q) carry no horizon: their latest instance is always current.
    # Both read paths (live EDGAR + backtest cache) honour this value, so
    # live and replay selections stay identical.  Sized from the 2026-06-11
    # 8-K volume check: watchlist tickers file ~4 8-Ks per 90 days (max 8),
    # so the worst-case analyst pane stays small.
    filings_8k_staleness_days:    int  = 90


class DataConfig(BaseModel):
    providers: dict[str, str]
    defaults: FetchDefaults = Field(default_factory=FetchDefaults)
    # Prefixed ``quiver_`` to reflect that only the Quiver Quant politician-trades
    # provider actually consumes this value.  A global ``http_timeout_seconds``
    # name implied it was project-wide; it is not.
    quiver_http_timeout_seconds: float = 15.0

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
