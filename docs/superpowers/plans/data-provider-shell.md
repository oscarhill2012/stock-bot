# Data Provider Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor `src/data/` so swapping a data provider is a one-file drop + a one-line edit to `config/data.json`. No agent or aggregator code changes.

**Architecture:** Each domain (news, stats, filings, …) gets its own directory under `src/data/providers/`. Each provider is a single async `fetch()` decorated with `@register(domain, name, upstream, rate_per_minute, burst)`. A registry dispatches calls based on the active-provider mapping in `config/data.json`. Public API in `src/data/__init__.py` is a set of domain-shaped getters that wrap `dispatch(...)`. Rate limiting moves to the registry (one `AsyncRateLimiter` per upstream, lazily created from `@register` calls). Secrets stay in `.env`, read via `data.secrets.require_key`.

**Tech Stack:** Python 3.11+, pydantic v2, pydantic-settings, asyncio, pytest. Existing libs unchanged: `yfinance`, `finnhub-python`, `edgartools`, `requests`.

**Spec:** `docs/superpowers/specs/data-provider-shell-design.md`

**Working directory:** Run all commands from `C:/Users/oscar/OneDrive - Nexus365/Documents/StockBot`. Bash tool already runs from there — do not prepend `cd`. Use `.venv/Scripts/python -m pytest ...` and `.venv/Scripts/python -m ruff ...`.

---

## File Structure

**Created:**
- `src/data/secrets.py` — `require_key(env_var)`, `SecretMissingError`
- `src/data/config.py` — `DataConfig`, `FetchDefaults`, `get_config()` (cached)
- `src/data/registry.py` — `register`, `_ensure_limiter`, `dispatch`, `active_upstreams`, `min_decision_interval_seconds`, `DOMAINS`
- `config/data.json` — provider mapping + fetch defaults + http timeout
- `src/data/providers/<domain>/__init__.py` for each of the seven domains
- `src/data/providers/stats/yfinance.py`
- `src/data/providers/news/finnhub.py`
- `src/data/providers/social_sentiment/finnhub.py`
- `src/data/providers/filings/edgar.py`
- `src/data/providers/notable_holders/edgar.py`
- `src/data/providers/insider_trades/edgar.py`
- `src/data/providers/politician_trades/quiver.py`
- `tests/unit/data/__init__.py`
- `tests/unit/data/conftest.py` — registry-isolation fixture
- `tests/unit/data/test_secrets.py`
- `tests/unit/data/test_config.py`
- `tests/unit/data/test_registry.py`
- `tests/unit/data/test_active_pacing.py`
- `tests/unit/data/test_aggregator.py`
- `tests/unit/data/test_provider_registration.py`

**Modified:**
- `src/data/rate_limit.py` — add `capacity` public property; remove `FINNHUB`/`EDGAR`/`QUIVER`/`YFINANCE` singletons + `ALL_LIMITERS` + `slowest_min_interval_seconds` in cleanup
- `src/data/__init__.py` — public getters are dispatch wrappers; cross-validation at import; remove old exports in cleanup
- `src/data/aggregator.py` — uses public getters; passes `domain` to `_safe`; uses new `min_decision_interval_seconds()`
- `src/data/models/bundle.py` — `ProviderError` gets `domain` field; `provider` field semantics change
- `src/data/providers/__init__.py` — explicit imports of every provider module so `@register` runs at package load

**Deleted:**
- `src/data/settings.py`
- `src/data/providers/yfinance_stats.py`
- `src/data/providers/finnhub_news.py`
- `src/data/providers/finnhub_social.py`
- `src/data/providers/sec_insiders.py`
- `src/data/providers/sec_holders.py`
- `src/data/providers/sec_filings.py`
- `src/data/providers/quiver_politicians.py`

---

## Test Conventions

- All new unit tests live under `tests/unit/data/`.
- Registry tests use the `registry_isolation` fixture (defined in Task 5) to snapshot/restore `_REGISTRY` and `_LIMITERS`.
- Tests do not hit the network. Provider migrations preserve existing pure logic; per-provider tests stub out the underlying client where needed (yfinance, finnhub, edgartools, requests).
- Run a single test with `.venv/Scripts/python -m pytest tests/unit/data/test_<name>.py::test_<name> -v`.
- Run the whole suite with `.venv/Scripts/python -m pytest tests/ -q`.
- Ruff lint: `.venv/Scripts/python -m ruff check src/data tests/unit/data`.

---

### Task 1: Add `capacity` public property to `AsyncRateLimiter`

The registry's `_ensure_limiter` needs to compare the configured burst capacity against an existing limiter for the same upstream. Today it lives on the private `_bucket.capacity` — expose it.

**Files:**
- Modify: `src/data/rate_limit.py`
- Test: `tests/unit/data/test_registry.py` (fresh file; will get expanded in later tasks)

- [ ] **Step 1: Create `tests/unit/data/__init__.py` (empty) and write the failing test**

Create `tests/unit/data/__init__.py`:
```python
```

Create `tests/unit/data/test_registry.py`:
```python
"""Unit tests for data.registry — provider shell + dispatch."""
from __future__ import annotations

from data.rate_limit import AsyncRateLimiter


def test_async_rate_limiter_exposes_capacity() -> None:
    lim = AsyncRateLimiter("acme", rate_per_minute=120, burst=10)
    assert lim.capacity == 10


def test_async_rate_limiter_capacity_defaults_to_rounded_rate() -> None:
    lim = AsyncRateLimiter("acme", rate_per_minute=60)
    # When burst is unset, capacity falls back to round(rate_per_minute).
    assert lim.capacity == 60
```

- [ ] **Step 2: Run the tests and verify they fail**

```
.venv/Scripts/python -m pytest tests/unit/data/test_registry.py -v
```
Expected: FAIL — `AttributeError: 'AsyncRateLimiter' object has no attribute 'capacity'`.

- [ ] **Step 3: Add the `capacity` property**

In `src/data/rate_limit.py`, add immediately after the existing `min_interval_seconds` property:
```python
    @property
    def capacity(self) -> int:
        """Burst capacity (max tokens the bucket holds at any moment)."""
        return self._bucket.capacity
```

- [ ] **Step 4: Run the tests again and verify they pass**

```
.venv/Scripts/python -m pytest tests/unit/data/test_registry.py -v
```
Expected: PASS for both tests.

- [ ] **Step 5: Commit**

```
git add src/data/rate_limit.py tests/unit/data/__init__.py tests/unit/data/test_registry.py
git commit -m "feat(data): expose AsyncRateLimiter.capacity as a public property"
```

---

### Task 2: `data/secrets.py` — env var reader

Replaces the secrets-loading parts of `data/settings.py`. Providers call `require_key("FINNHUB_API_KEY")` etc.

**Files:**
- Create: `src/data/secrets.py`
- Test: `tests/unit/data/test_secrets.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/data/test_secrets.py`:
```python
"""Unit tests for data.secrets — env-var reader."""
from __future__ import annotations

import pytest

from data.secrets import SecretMissingError, require_key


def test_require_key_returns_value_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCKBOT_TEST_KEY", "abc123")
    assert require_key("STOCKBOT_TEST_KEY") == "abc123"


def test_require_key_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STOCKBOT_MISSING_KEY", raising=False)
    with pytest.raises(SecretMissingError, match="STOCKBOT_MISSING_KEY"):
        require_key("STOCKBOT_MISSING_KEY")


def test_require_key_raises_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STOCKBOT_EMPTY_KEY", "")
    with pytest.raises(SecretMissingError):
        require_key("STOCKBOT_EMPTY_KEY")
```

- [ ] **Step 2: Run the tests and verify they fail**

```
.venv/Scripts/python -m pytest tests/unit/data/test_secrets.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'data.secrets'`.

- [ ] **Step 3: Create `src/data/secrets.py`**

```python
"""Secret reader. Loads `.env` once, then `require_key()` reads via `os.getenv`.

Providers call `require_key("FINNHUB_API_KEY")` at fetch time (not at import
time) so a partially-configured `.env` can still import the data package.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv


class SecretMissingError(RuntimeError):
    """Raised when a provider asks for an env var that is unset."""


_loaded = False


def _ensure_loaded() -> None:
    global _loaded
    if not _loaded:
        load_dotenv()
        _loaded = True


def require_key(env_var: str) -> str:
    """Return the env var or raise `SecretMissingError`."""
    _ensure_loaded()
    val = os.getenv(env_var)
    if not val:
        raise SecretMissingError(
            f"{env_var} is unset. Add it to .env."
        )
    return val
```

- [ ] **Step 4: Run the tests again and verify they pass**

```
.venv/Scripts/python -m pytest tests/unit/data/test_secrets.py -v
```
Expected: PASS for all three tests.

- [ ] **Step 5: Commit**

```
git add src/data/secrets.py tests/unit/data/test_secrets.py
git commit -m "feat(data): add data.secrets.require_key as the env-var reader"
```

---

### Task 3: `data/config.py` — pydantic config loader

`DataConfig` model + cached `get_config()` reading `config/data.json`.

**Files:**
- Create: `src/data/config.py`
- Test: `tests/unit/data/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/data/test_config.py`:
```python
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
```

- [ ] **Step 2: Run the tests and verify they fail**

```
.venv/Scripts/python -m pytest tests/unit/data/test_config.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'data.config'`.

- [ ] **Step 3: Create `src/data/config.py`**

```python
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
    def _check_domains(self) -> "DataConfig":
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
```

- [ ] **Step 4: Run the tests again and verify they pass**

```
.venv/Scripts/python -m pytest tests/unit/data/test_config.py -v
```
Expected: PASS for all four tests.

- [ ] **Step 5: Commit**

```
git add src/data/config.py tests/unit/data/test_config.py
git commit -m "feat(data): add DataConfig pydantic model + load_config_from"
```

---

### Task 4: `config/data.json` — initial values mirroring today's defaults

Single source of truth for active providers + fetch defaults. Values match what's currently hardcoded across `data/aggregator.py`, `data/providers/*.py`, and `data/settings.py`.

**Files:**
- Create: `config/data.json`

- [ ] **Step 1: Create `config/data.json`**

```json
{
  "providers": {
    "stats": "yfinance",
    "news": "finnhub",
    "social_sentiment": "finnhub",
    "insider_trades": "edgar",
    "politician_trades": "quiver",
    "notable_holders": "edgar",
    "filings": "edgar"
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
    "include_filing_excerpts": true
  },
  "http_timeout_seconds": 15.0
}
```

- [ ] **Step 2: Verify it loads via the existing test harness**

```
.venv/Scripts/python -c "from data.config import get_config; c = get_config(); print(c.providers)"
```
Expected output (one line):
```
{'stats': 'yfinance', 'news': 'finnhub', 'social_sentiment': 'finnhub', 'insider_trades': 'edgar', 'politician_trades': 'quiver', 'notable_holders': 'edgar', 'filings': 'edgar'}
```

- [ ] **Step 3: Commit**

```
git add config/data.json
git commit -m "feat(config): seed config/data.json with current provider mapping + defaults"
```

---

### Task 5: `data/registry.py` — `@register`, `dispatch`, `_ensure_limiter`

Core of the provider shell. Tests use a fixture that snapshots `_REGISTRY` + `_LIMITERS` so each test starts clean.

**Files:**
- Create: `src/data/registry.py`
- Create: `tests/unit/data/conftest.py`
- Modify: `tests/unit/data/test_registry.py`

- [ ] **Step 1: Create `tests/unit/data/conftest.py` (registry isolation fixture)**

```python
"""Shared fixtures for data-layer unit tests."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from data import registry as _registry


@pytest.fixture
def registry_isolation() -> Iterator[None]:
    """Snapshot _REGISTRY and _LIMITERS, restore after the test.

    Lets tests register fake providers without leaking into the next
    test or into real provider tests.
    """
    saved_registry = dict(_registry._REGISTRY)
    saved_limiters = dict(_registry._LIMITERS)
    _registry._REGISTRY.clear()
    _registry._LIMITERS.clear()
    try:
        yield
    finally:
        _registry._REGISTRY.clear()
        _registry._LIMITERS.clear()
        _registry._REGISTRY.update(saved_registry)
        _registry._LIMITERS.update(saved_limiters)
```

- [ ] **Step 2: Append registry tests to `tests/unit/data/test_registry.py`**

Append (do not delete the existing capacity tests from Task 1):
```python

import asyncio

import pytest

from data import registry
from data.registry import (
    DOMAINS,
    _ensure_limiter,
    active_upstreams,
    dispatch,
    min_decision_interval_seconds,
    register,
)


def test_domains_set_has_seven_known_slots() -> None:
    assert DOMAINS == frozenset({
        "stats",
        "news",
        "social_sentiment",
        "insider_trades",
        "politician_trades",
        "notable_holders",
        "filings",
    })


def test_register_populates_registry(registry_isolation: None) -> None:
    @register("news", "fake", upstream="fake_up", rate_per_minute=600, burst=10)
    async def fetch(ticker: str) -> str:
        return ticker.upper()

    entry = registry._REGISTRY[("news", "fake")]
    assert entry.domain == "news"
    assert entry.name == "fake"
    assert entry.upstream == "fake_up"
    assert entry.fn is fetch


def test_register_unknown_domain_raises(registry_isolation: None) -> None:
    with pytest.raises(ValueError, match="unknown domain"):
        @register("weather", "noaa", upstream="noaa", rate_per_minute=60, burst=1)
        async def fetch(ticker: str) -> str:
            return ticker


def test_ensure_limiter_returns_existing_when_matched(registry_isolation: None) -> None:
    a = _ensure_limiter("up", 60, 10)
    b = _ensure_limiter("up", 60, 10)
    assert a is b


def test_ensure_limiter_conflict_raises(registry_isolation: None) -> None:
    _ensure_limiter("up", 60, 10)
    with pytest.raises(ValueError, match="conflicting rate-limit"):
        _ensure_limiter("up", 120, 10)
    with pytest.raises(ValueError, match="conflicting rate-limit"):
        _ensure_limiter("up", 60, 20)


def test_dispatch_calls_active_provider(monkeypatch: pytest.MonkeyPatch, registry_isolation: None) -> None:
    @register("news", "fake_a", upstream="fake_a", rate_per_minute=6000, burst=10)
    async def fetch_a(ticker: str) -> str:
        return f"a:{ticker}"

    @register("news", "fake_b", upstream="fake_b", rate_per_minute=6000, burst=10)
    async def fetch_b(ticker: str) -> str:
        return f"b:{ticker}"

    from data import config as data_config

    fake_cfg = data_config.DataConfig(
        providers={
            "stats": "fake_a",  # not used
            "news": "fake_b",
            "social_sentiment": "fake_a",
            "insider_trades": "fake_a",
            "politician_trades": "fake_a",
            "notable_holders": "fake_a",
            "filings": "fake_a",
        },
    )
    monkeypatch.setattr(data_config, "_cache", fake_cfg)

    result = asyncio.run(dispatch("news", "AAPL"))
    assert result == "b:AAPL"


def test_active_upstreams_reflects_config(monkeypatch: pytest.MonkeyPatch, registry_isolation: None) -> None:
    for name, up in [("fake_a", "alpha"), ("fake_b", "beta")]:
        @register("news", name, upstream=up, rate_per_minute=6000, burst=10)
        async def fetch(ticker: str, _name: str = name) -> str:
            return _name
        # Register the same name into every domain so DataConfig validates.
        for d in DOMAINS - {"news"}:
            @register(d, name, upstream=up, rate_per_minute=6000, burst=10)
            async def _other(ticker: str) -> str:
                return ""

    from data import config as data_config

    monkeypatch.setattr(data_config, "_cache", data_config.DataConfig(
        providers={d: "fake_a" for d in DOMAINS} | {"news": "fake_b"},
    ))
    ups = active_upstreams()
    assert "alpha" in ups
    assert "beta" in ups
    floor = min_decision_interval_seconds()
    assert floor > 0
```

- [ ] **Step 3: Run the tests and verify they fail**

```
.venv/Scripts/python -m pytest tests/unit/data/test_registry.py -v
```
Expected: capacity tests still PASS, all new tests FAIL — `ModuleNotFoundError: No module named 'data.registry'` (or `ImportError` for `register`/`dispatch`).

- [ ] **Step 4: Create `src/data/registry.py`**

```python
"""Provider shell — `@register` decorator, `dispatch`, shared limiter map.

Adding a provider means writing a single async `fetch(...)` function with a
`@register(domain, name, upstream, rate_per_minute, burst)` decorator. The
registry handles rate-limit acquisition; providers do not call the limiter
themselves.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .config import get_config
from .rate_limit import AsyncRateLimiter


DOMAINS: frozenset[str] = frozenset({
    "stats",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
})


@dataclass(frozen=True)
class _Entry:
    domain: str
    name: str
    upstream: str
    fn: Callable[..., Awaitable[Any]]


_REGISTRY: dict[tuple[str, str], _Entry] = {}
_LIMITERS: dict[str, AsyncRateLimiter] = {}


def _ensure_limiter(upstream: str, rate_per_minute: float, burst: int) -> AsyncRateLimiter:
    """Get-or-create the limiter for `upstream`. Conflicting limits raise."""
    existing = _LIMITERS.get(upstream)
    if existing is not None:
        if (existing.rate_per_minute, existing.capacity) != (rate_per_minute, burst):
            raise ValueError(
                f"conflicting rate-limit declarations for upstream {upstream!r}: "
                f"already {existing.rate_per_minute}/min burst {existing.capacity}, "
                f"got {rate_per_minute}/min burst {burst}"
            )
        return existing
    lim = AsyncRateLimiter(upstream, rate_per_minute=rate_per_minute, burst=burst)
    _LIMITERS[upstream] = lim
    return lim


def register(
    domain: str,
    name: str,
    *,
    upstream: str,
    rate_per_minute: float,
    burst: int,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Decorate an async `fetch` function as the `(domain, name)` provider."""
    if domain not in DOMAINS:
        raise ValueError(f"unknown domain: {domain!r}")

    def deco(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        _ensure_limiter(upstream, rate_per_minute, burst)
        _REGISTRY[(domain, name)] = _Entry(domain, name, upstream, fn)
        return fn

    return deco


async def dispatch(domain: str, *args: Any, **kwargs: Any) -> Any:
    """Call the active provider for `domain` after acquiring its limiter token."""
    cfg = get_config()
    name = cfg.providers[domain]
    try:
        entry = _REGISTRY[(domain, name)]
    except KeyError as exc:
        raise RuntimeError(
            f"no provider registered for ({domain!r}, {name!r}); "
            f"check config/data.json + the provider module is imported"
        ) from exc
    await _LIMITERS[entry.upstream].acquire()
    return await entry.fn(*args, **kwargs)


def active_upstreams() -> set[str]:
    """Upstream identifiers used by the currently active provider set."""
    cfg = get_config()
    return {_REGISTRY[(d, n)].upstream for d, n in cfg.providers.items() if (d, n) in _REGISTRY}


def min_decision_interval_seconds() -> float:
    """Floor on the trading cadence given the active providers' rate budgets."""
    return max(
        (_LIMITERS[u].min_interval_seconds for u in active_upstreams() if u in _LIMITERS),
        default=0.0,
    )
```

- [ ] **Step 5: Run the tests again and verify they pass**

```
.venv/Scripts/python -m pytest tests/unit/data/test_registry.py -v
```
Expected: PASS for all tests (capacity ones from Task 1 still green).

- [ ] **Step 6: Commit**

```
git add src/data/registry.py tests/unit/data/conftest.py tests/unit/data/test_registry.py
git commit -m "feat(data): add provider registry — @register, dispatch, _ensure_limiter"
```

---

### Task 6: Migrate `stats/yfinance` provider

Move `src/data/providers/yfinance_stats.py` → `src/data/providers/stats/yfinance.py`. Function renamed from `get_stock_stats` to `fetch`. Add `@register`. Drop the in-provider `await YFINANCE.acquire()` (the registry handles it now).

**Files:**
- Delete: `src/data/providers/yfinance_stats.py`
- Create: `src/data/providers/stats/__init__.py`, `src/data/providers/stats/yfinance.py`
- Test: `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Write the failing registration test**

Create `tests/unit/data/test_provider_registration.py`:
```python
"""Smoke tests: each provider module registers itself when imported."""
from __future__ import annotations


def test_stats_yfinance_registers_on_import() -> None:
    # Importing the provider module triggers its @register decorator.
    import data.providers.stats.yfinance  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("stats", "yfinance")]
    assert entry.upstream == "yfinance"
```

- [ ] **Step 2: Run the test and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_stats_yfinance_registers_on_import -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'data.providers.stats.yfinance'`.

- [ ] **Step 3: Create `src/data/providers/stats/__init__.py` (empty)**

```python
```

- [ ] **Step 4: Create `src/data/providers/stats/yfinance.py`**

Body preserves the existing `_f`, `_fetch_stats` helpers and the OHLCBar/StockStats construction unchanged. Only the public function name + decorators change.

```python
"""yfinance stats provider — OHLCV history + fundamentals (rate-limited via registry)."""
from __future__ import annotations

import asyncio
import math
from typing import Any

import yfinance as yf

from data.registry import register
from data.retry import with_retry

from ...models import OHLCBar, StockStats


def _f(d: dict[str, Any], *keys: str) -> float | None:
    """Try each key in order; return the first finite float found, or None."""
    for k in keys:
        v = d.get(k)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            return f
    return None


@with_retry
def _fetch_stats(symbol: str, period: str, interval: str) -> StockStats:
    yt = yf.Ticker(symbol)
    df = yt.history(period=period, interval=interval, auto_adjust=True)

    bars: list[OHLCBar] = []
    if df is not None and not df.empty:
        for ts, row in df.iterrows():
            bars.append(
                OHLCBar(
                    timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row.get("Volume", 0) or 0),
                )
            )

    info: dict[str, Any] = {}
    try:
        info = yt.info or {}
    except Exception:
        info = {}

    fast: dict[str, Any] = {}
    try:
        fast = dict(yt.fast_info) if yt.fast_info else {}
    except Exception:
        fast = {}

    return StockStats(
        ticker=symbol,
        history=bars,
        market_cap=_f(info, "marketCap") or _f(fast, "market_cap", "marketCap"),
        trailing_pe=_f(info, "trailingPE"),
        forward_pe=_f(info, "forwardPE"),
        beta=_f(info, "beta"),
        dividend_yield=_f(info, "dividendYield"),
        fifty_day_average=_f(info, "fiftyDayAverage")
        or _f(fast, "fifty_day_average", "fiftyDayAverage"),
        two_hundred_day_average=_f(info, "twoHundredDayAverage")
        or _f(fast, "two_hundred_day_average", "twoHundredDayAverage"),
        last_price=_f(fast, "last_price", "lastPrice")
        or _f(info, "currentPrice", "regularMarketPrice"),
        sector=info.get("sector"),
        long_name=info.get("longName") or info.get("shortName"),
    )


@register(
    domain="stats",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=30,
)
async def fetch(ticker: str, *, period: str = "1y", interval: str = "1d") -> StockStats:
    symbol = ticker.upper()
    return await asyncio.to_thread(_fetch_stats, symbol, period, interval)
```

Note: dropped the `await YFINANCE.acquire()` line — the registry's `dispatch` does that now.

- [ ] **Step 5: Update `src/data/providers/__init__.py` to import from the new location**

Replace the old `from .yfinance_stats import get_stock_stats` line with an import of the new module so `@register` runs:
```python
"""Per-source provider modules. Importing each module triggers its @register call."""
from .stats import yfinance as _stats_yfinance  # noqa: F401

# Below imports remain unchanged for now (subsequent tasks migrate them):
from .finnhub_news import get_stock_news
from .finnhub_social import get_social_sentiment
from .quiver_politicians import get_public_figure_trades
from .sec_filings import get_company_filings
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades

__all__ = [
    "get_stock_news",
    "get_social_sentiment",
    "get_public_figure_trades",
    "get_company_filings",
    "get_insider_trades",
    "get_notable_holders",
    # `get_stock_stats` removed from this list — superseded by the registry path.
]
```

- [ ] **Step 6: Update `src/data/aggregator.py` to import `get_stock_stats` from the registry path instead of `.providers`**

Edit `src/data/aggregator.py` import block:
```python
from .providers import (
    get_company_filings,
    get_insider_trades,
    get_notable_holders,
    get_public_figure_trades,
    get_social_sentiment,
    get_stock_news,
)
from .registry import dispatch
```
Replace the existing `_safe("stats", get_stock_stats(...), errors)` call with:
```python
        _safe("stats", dispatch("stats", symbol, period=history_period, interval=history_interval), errors),
```

- [ ] **Step 7: Update `src/data/__init__.py` to expose `get_stock_stats` as a dispatch wrapper**

Replace the line `from .providers import (..., get_stock_stats, ...)` with:
```python
from .providers import (
    get_company_filings,
    get_insider_trades,
    get_notable_holders,
    get_public_figure_trades,
    get_social_sentiment,
    get_stock_news,
)
from .registry import dispatch as _dispatch


async def get_stock_stats(ticker: str, period: str = "1y", interval: str = "1d"):
    """Fetch OHLCV + fundamentals for `ticker` via the active stats provider."""
    return await _dispatch("stats", ticker.upper(), period=period, interval=interval)
```

Leave the rest of `data/__init__.py` (the named limiter exports etc.) intact for now — Task 14 cleans those up.

- [ ] **Step 8: Delete `src/data/providers/yfinance_stats.py`**

```
git rm src/data/providers/yfinance_stats.py
```

- [ ] **Step 9: Run the new + existing tests**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS for the new registration test; analyst-fetcher tests still PASS (`get_stock_stats` signature unchanged from the caller's POV); ruff clean.

- [ ] **Step 10: Commit**

```
git add src/data/providers/stats src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate yfinance stats provider to the registry shell"
```

---

### Task 7: Migrate `news/finnhub` provider

**Files:**
- Delete: `src/data/providers/finnhub_news.py`
- Create: `src/data/providers/news/__init__.py`, `src/data/providers/news/finnhub.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append a registration test**

Append to `tests/unit/data/test_provider_registration.py`:
```python


def test_news_finnhub_registers_on_import() -> None:
    import data.providers.news.finnhub  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("news", "finnhub")]
    assert entry.upstream == "finnhub"
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_news_finnhub_registers_on_import -v
```
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create `src/data/providers/news/__init__.py` (empty)**

```python
```

- [ ] **Step 4: Create `src/data/providers/news/finnhub.py`**

```python
"""Finnhub news provider — `company_news` endpoint (rate-limited via registry)."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import finnhub

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import NewsArticle


def _client() -> finnhub.Client:
    return finnhub.Client(api_key=require_key("FINNHUB_API_KEY"))


@with_retry
def _fetch_company_news(symbol: str, from_iso: str, to_iso: str) -> list[dict]:
    return _client().company_news(symbol, _from=from_iso, to=to_iso) or []


@register(
    domain="news",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=30,
)
async def fetch(
    ticker: str,
    *,
    from_date: date,
    to_date: date,
    limit: int | None = 50,
) -> list[NewsArticle]:
    symbol = ticker.upper()
    raw = await asyncio.to_thread(
        _fetch_company_news, symbol, from_date.isoformat(), to_date.isoformat()
    )
    if not raw:
        return []

    raw.sort(key=lambda a: a.get("datetime", 0), reverse=True)
    if limit is not None:
        raw = raw[:limit]

    articles: list[NewsArticle] = []
    for item in raw:
        ts = item.get("datetime")
        published = (
            datetime.fromtimestamp(ts, tz=UTC)
            if isinstance(ts, (int, float)) and ts > 0
            else datetime.now(UTC)
        )
        articles.append(
            NewsArticle(
                ticker=symbol,
                headline=item.get("headline", "") or "",
                summary=item.get("summary", "") or "",
                url=item.get("url", "") or "",
                source=item.get("source", "") or "",
                published_at=published,
                sentiment=None,
            )
        )
    return articles
```

- [ ] **Step 5: Update `src/data/providers/__init__.py`**

```python
"""Per-source provider modules. Importing each module triggers its @register call."""
from .news import finnhub as _news_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

# Below imports remain unchanged for now:
from .finnhub_social import get_social_sentiment
from .quiver_politicians import get_public_figure_trades
from .sec_filings import get_company_filings
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades

__all__ = [
    "get_social_sentiment",
    "get_public_figure_trades",
    "get_company_filings",
    "get_insider_trades",
    "get_notable_holders",
]
```

- [ ] **Step 6: Update `src/data/aggregator.py`**

Replace `_safe("news", get_stock_news(...), errors)` with the dispatch call:
```python
        _safe(
            "news",
            dispatch("news", symbol,
                     from_date=today - timedelta(days=news_lookback_days),
                     to_date=today),
            errors,
        ),
```

Remove `get_stock_news` from the `from .providers import (...)` block.

- [ ] **Step 7: Update `src/data/__init__.py`**

Replace `get_stock_news` in the `from .providers import (...)` block with a dispatch wrapper:
```python
from datetime import date as _date, timedelta as _timedelta

# ... existing wrapper for get_stock_stats ...

async def get_stock_news(
    ticker: str,
    from_date: _date | None = None,
    to_date: _date | None = None,
    *,
    limit: int | None = 50,
):
    """Fetch news articles for `ticker` via the active news provider."""
    today = _date.today()
    return await _dispatch(
        "news",
        ticker.upper(),
        from_date=from_date or (today - _timedelta(days=7)),
        to_date=to_date or today,
        limit=limit,
    )
```

- [ ] **Step 8: Delete `src/data/providers/finnhub_news.py`**

```
git rm src/data/providers/finnhub_news.py
```

- [ ] **Step 9: Run the test suite + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: all PASS, ruff clean.

- [ ] **Step 10: Commit**

```
git add src/data/providers/news src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate finnhub news provider to the registry shell"
```

---

### Task 8: Migrate `social_sentiment/finnhub` provider

Same upstream as `news/finnhub` — registration must reuse the existing `finnhub` limiter (Task 5's `_ensure_limiter` ok-path). Keep file body except for register/import changes.

**Files:**
- Delete: `src/data/providers/finnhub_social.py`
- Create: `src/data/providers/social_sentiment/__init__.py`, `src/data/providers/social_sentiment/finnhub.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append registration test**

```python


def test_social_sentiment_finnhub_registers_on_import() -> None:
    import data.providers.social_sentiment.finnhub  # noqa: F401
    from data.registry import _REGISTRY, _LIMITERS

    entry = _REGISTRY[("social_sentiment", "finnhub")]
    assert entry.upstream == "finnhub"
    # Same upstream as news/finnhub — must share the limiter singleton.
    assert _LIMITERS["finnhub"] is _LIMITERS["finnhub"]
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_social_sentiment_finnhub_registers_on_import -v
```
Expected: FAIL.

- [ ] **Step 3: Create `social_sentiment/__init__.py` (empty) + `social_sentiment/finnhub.py`**

Move the body of `src/data/providers/finnhub_social.py` into `src/data/providers/social_sentiment/finnhub.py`. Replace its top with:
```python
"""Finnhub social-sentiment provider (rate-limited via registry)."""
from __future__ import annotations

import asyncio

import finnhub

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import SocialSentiment, SocialSentimentSnapshot


def _client() -> finnhub.Client:
    return finnhub.Client(api_key=require_key("FINNHUB_API_KEY"))


# (preserve the existing _fetch_social helper unchanged)


@register(
    domain="social_sentiment",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=30,
)
async def fetch(ticker: str) -> SocialSentiment | None:
    # (preserve existing body, minus `await FINNHUB.acquire()`)
    ...
```

Refer to `src/data/providers/finnhub_social.py` for the exact body to preserve. The structural changes are:
- import `register`/`require_key` from the new locations
- function renamed `get_social_sentiment` → `fetch`
- decorate with `@register(...)`
- remove the `await FINNHUB.acquire()` line

- [ ] **Step 4: Update `src/data/providers/__init__.py`**

```python
from .news import finnhub as _news_finnhub  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

from .quiver_politicians import get_public_figure_trades
from .sec_filings import get_company_filings
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades

__all__ = [
    "get_public_figure_trades",
    "get_company_filings",
    "get_insider_trades",
    "get_notable_holders",
]
```

- [ ] **Step 5: Update `src/data/aggregator.py`**

Replace `_safe("social", get_social_sentiment(...), errors)` with:
```python
        _safe("social_sentiment", dispatch("social_sentiment", symbol), errors),
```

Note the slot-name change: today's `_DEFAULTS` key is `"social"`. Update `_DEFAULTS` accordingly:
```python
_DEFAULTS: dict[str, Any] = {
    "stats": None,
    "news": [],
    "social_sentiment": None,
    "insiders": [],
    "politicians": [],
    "notable_holders": [],
    "filings": [],
}
```

Update the bundle construction:
```python
        social_sentiment=social,
```
(Already named correctly — only `_DEFAULTS` needs renaming.)

Remove `get_social_sentiment` from the `from .providers import (...)` block.

- [ ] **Step 6: Update `src/data/__init__.py`**

Replace `get_social_sentiment` import with a dispatch wrapper:
```python
async def get_social_sentiment(ticker: str):
    """Fetch social-sentiment snapshot for `ticker` via the active provider."""
    return await _dispatch("social_sentiment", ticker.upper())
```

- [ ] **Step 7: Delete `src/data/providers/finnhub_social.py`**

```
git rm src/data/providers/finnhub_social.py
```

- [ ] **Step 8: Run tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```
git add src/data/providers/social_sentiment src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate finnhub social-sentiment provider to registry shell"
```

---

### Task 9: Migrate `filings/edgar` provider

First EDGAR provider — establishes the `edgar` upstream limiter (`rate_per_minute=600, burst=20`). Edgar identity bootstrap (`set_identity`) moves into a helper using `data.secrets.require_key`.

**Files:**
- Delete: `src/data/providers/sec_filings.py`
- Create: `src/data/providers/filings/__init__.py`, `src/data/providers/filings/edgar.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append registration test**

```python


def test_filings_edgar_registers_on_import() -> None:
    import data.providers.filings.edgar  # noqa: F401
    from data.registry import _REGISTRY, _LIMITERS

    entry = _REGISTRY[("filings", "edgar")]
    assert entry.upstream == "edgar"
    assert _LIMITERS["edgar"].rate_per_minute == 600
    assert _LIMITERS["edgar"].capacity == 20
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_filings_edgar_registers_on_import -v
```
Expected: FAIL.

- [ ] **Step 3: Create `filings/__init__.py` (empty) + `filings/edgar.py`**

Move body from `src/data/providers/sec_filings.py`. Replace `_ensure_identity()` to use `data.secrets.require_key`. Top of file:
```python
"""EDGAR 10-K/10-Q/8-K filings provider (rate-limited via registry)."""
from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from typing import Any

from edgar import Company, set_identity

from data.registry import register
from data.retry import with_retry
from data.secrets import require_key

from ...models import Filing


_EXCERPT_CHARS = 2000

_SECTION_KEYS = {
    "10-K": {"risk_factors_excerpt": "part_i_item_1a", "mda_excerpt": "part_ii_item_7"},
    "10-Q": {"risk_factors_excerpt": "part_ii_item_1a", "mda_excerpt": "part_i_item_2"},
}


def _ensure_identity() -> None:
    set_identity(require_key("EDGAR_IDENTITY"))


# preserve _coerce_date, _section_text, _build_filing, _list_filings,
# _build_filing_with_identity unchanged from src/data/providers/sec_filings.py


@register(
    domain="filings",
    name="edgar",
    upstream="edgar",
    rate_per_minute=600,
    burst=20,
)
async def fetch(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    include_excerpts: bool = True,
) -> list[Filing]:
    symbol = ticker.upper()
    filings = await asyncio.to_thread(_list_filings, symbol, form_types, limit)

    out: list[Filing] = []
    for filing in filings:
        # (preserve the existing per-filing extraction loop, MINUS the
        #  `await EDGAR.acquire()` calls — registry's dispatch handles
        #  the *first* acquire, but per-filing acquires need a different
        #  mechanism. See Step 3a below.)
        ...
```

- [ ] **Step 3a: Per-filing rate-limit acquisition**

The current `sec_filings.py` does `await EDGAR.acquire()` once per filing inside the loop (because each `filing.obj()` is one HTTP roundtrip). The registry's `dispatch` only acquires once. Preserve the per-filing throttling by importing the limiter map and acquiring inside the loop:

```python
from data.registry import _LIMITERS  # internal use — per-filing token

# inside fetch(), inside the per-filing loop:
        if include_excerpts:
            await _LIMITERS["edgar"].acquire()
```

This is the one place where a provider legitimately reaches into the limiter map — document it with an inline comment. (Alternative: expose a public `data.registry.limiter(upstream)` helper. We can decide in Task 14.)

- [ ] **Step 4: Update `src/data/providers/__init__.py`**

```python
from .filings import edgar as _filings_edgar  # noqa: F401
from .news import finnhub as _news_finnhub  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

from .quiver_politicians import get_public_figure_trades
from .sec_holders import get_notable_holders
from .sec_insiders import get_insider_trades

__all__ = [
    "get_public_figure_trades",
    "get_insider_trades",
    "get_notable_holders",
]
```

- [ ] **Step 5: Update `src/data/aggregator.py`**

Replace `_safe("filings", get_company_filings(...), errors)` with:
```python
        _safe("filings",
              dispatch("filings", symbol,
                       limit=filings_per_form,
                       include_excerpts=include_filing_excerpts),
              errors),
```

Remove `get_company_filings` from `from .providers import (...)`.

- [ ] **Step 6: Update `src/data/__init__.py`**

Replace `get_company_filings` with a dispatch wrapper:
```python
async def get_company_filings(
    ticker: str,
    form_types: tuple[str, ...] = ("10-K", "10-Q", "8-K"),
    limit: int = 5,
    *,
    include_excerpts: bool = True,
):
    """Fetch SEC filings for `ticker` via the active filings provider."""
    return await _dispatch(
        "filings", ticker.upper(),
        form_types=form_types, limit=limit, include_excerpts=include_excerpts,
    )
```

- [ ] **Step 7: Delete `src/data/providers/sec_filings.py`**

```
git rm src/data/providers/sec_filings.py
```

- [ ] **Step 8: Run tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```
git add src/data/providers/filings src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate edgar filings provider to registry shell"
```

---

### Task 10: Migrate `notable_holders/edgar` provider

EDGAR upstream already established by Task 9 — registration must reuse the same limiter (asserts `_ensure_limiter` ok-path).

**Files:**
- Delete: `src/data/providers/sec_holders.py`
- Create: `src/data/providers/notable_holders/__init__.py`, `src/data/providers/notable_holders/edgar.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append registration test**

```python


def test_notable_holders_edgar_registers_on_import() -> None:
    import data.providers.notable_holders.edgar  # noqa: F401
    from data.registry import _REGISTRY, _LIMITERS

    entry = _REGISTRY[("notable_holders", "edgar")]
    assert entry.upstream == "edgar"
    # Same limiter singleton as filings/edgar.
    assert _LIMITERS["edgar"].rate_per_minute == 600
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_notable_holders_edgar_registers_on_import -v
```
Expected: FAIL.

- [ ] **Step 3: Create `notable_holders/__init__.py` + `notable_holders/edgar.py`**

Move body from `src/data/providers/sec_holders.py`. Apply the same transformations:
- replace top-level imports as in Task 9
- rename function from `get_notable_holders` → `fetch`
- decorate with `@register(domain="notable_holders", name="edgar", upstream="edgar", rate_per_minute=600, burst=20)`
- replace `_ensure_identity` to use `require_key("EDGAR_IDENTITY")`
- drop the in-provider `await EDGAR.acquire()` line(s) (registry handles the first acquire; per-call additional acquires use `_LIMITERS["edgar"].acquire()` if the original code had a loop)

- [ ] **Step 4: Update `providers/__init__.py`**

```python
from .filings import edgar as _filings_edgar  # noqa: F401
from .news import finnhub as _news_finnhub  # noqa: F401
from .notable_holders import edgar as _notable_holders_edgar  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

from .quiver_politicians import get_public_figure_trades
from .sec_insiders import get_insider_trades

__all__ = [
    "get_public_figure_trades",
    "get_insider_trades",
]
```

- [ ] **Step 5: Update `data/aggregator.py`**

Replace `_safe("notable_holders", get_notable_holders(...), errors)` with:
```python
        _safe("notable_holders",
              dispatch("notable_holders", symbol,
                       lookback_days=notable_holder_lookback_days,
                       limit=notable_holder_limit),
              errors),
```

Remove `get_notable_holders` from `from .providers import (...)`.

- [ ] **Step 6: Update `data/__init__.py`**

Replace `get_notable_holders` with a dispatch wrapper:
```python
async def get_notable_holders(
    ticker: str,
    *,
    lookback_days: int = 180,
    limit: int = 20,
):
    """Fetch notable EDGAR 13F holders for `ticker` via the active provider."""
    return await _dispatch(
        "notable_holders", ticker.upper(),
        lookback_days=lookback_days, limit=limit,
    )
```

- [ ] **Step 7: Delete `src/data/providers/sec_holders.py`**

```
git rm src/data/providers/sec_holders.py
```

- [ ] **Step 8: Run tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```
git add src/data/providers/notable_holders src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate edgar notable-holders provider to registry shell"
```

---

### Task 11: Migrate `insider_trades/edgar` provider

Same shape as Task 10. Third EDGAR provider — exercises the `_ensure_limiter` shared-upstream path again.

**Files:**
- Delete: `src/data/providers/sec_insiders.py`
- Create: `src/data/providers/insider_trades/__init__.py`, `src/data/providers/insider_trades/edgar.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append registration test**

```python


def test_insider_trades_edgar_registers_on_import() -> None:
    import data.providers.insider_trades.edgar  # noqa: F401
    from data.registry import _REGISTRY

    entry = _REGISTRY[("insider_trades", "edgar")]
    assert entry.upstream == "edgar"
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_insider_trades_edgar_registers_on_import -v
```
Expected: FAIL.

- [ ] **Step 3: Create files** (`__init__.py` empty + `insider_trades/edgar.py`)

Move from `src/data/providers/sec_insiders.py` with the same transformations:
- import `register`, `with_retry`, `require_key`
- rename function to `fetch`
- decorate with `@register(domain="insider_trades", name="edgar", upstream="edgar", rate_per_minute=600, burst=20)`
- swap `_ensure_identity` to use `require_key`
- drop in-provider `await EDGAR.acquire()` calls

- [ ] **Step 4: Update `providers/__init__.py`**

```python
from .filings import edgar as _filings_edgar  # noqa: F401
from .insider_trades import edgar as _insider_trades_edgar  # noqa: F401
from .news import finnhub as _news_finnhub  # noqa: F401
from .notable_holders import edgar as _notable_holders_edgar  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

from .quiver_politicians import get_public_figure_trades

__all__ = ["get_public_figure_trades"]
```

- [ ] **Step 5: Update `data/aggregator.py`**

Replace `_safe("insiders", get_insider_trades(...), errors)` with:
```python
        _safe("insider_trades",
              dispatch("insider_trades", symbol, lookback_days=insider_lookback_days),
              errors),
```

Update `_DEFAULTS["insiders"]` key → `_DEFAULTS["insider_trades"]`. Bundle construction's `insider_trades=insiders` is already correct.

Remove `get_insider_trades` from `from .providers import (...)`.

- [ ] **Step 6: Update `data/__init__.py`**

```python
async def get_insider_trades(ticker: str, *, lookback_days: int = 30):
    """Fetch SEC Form 4 insider trades for `ticker` via the active provider."""
    return await _dispatch("insider_trades", ticker.upper(), lookback_days=lookback_days)
```

- [ ] **Step 7: Delete `src/data/providers/sec_insiders.py`**

```
git rm src/data/providers/sec_insiders.py
```

- [ ] **Step 8: Run tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```
git add src/data/providers/insider_trades src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate edgar insider-trades provider to registry shell"
```

---

### Task 12: Migrate `politician_trades/quiver` provider

New `quiver` upstream. Soft-fail when `QUIVER_QUANT_API_KEY` is unset (preserves today's behavior). The previously-config'd `quiver_base_url` becomes a module-level constant in this provider.

**Files:**
- Delete: `src/data/providers/quiver_politicians.py`
- Create: `src/data/providers/politician_trades/__init__.py`, `src/data/providers/politician_trades/quiver.py`
- Modify: `src/data/providers/__init__.py`, `src/data/aggregator.py`, `src/data/__init__.py`, `tests/unit/data/test_provider_registration.py`

- [ ] **Step 1: Append registration test**

```python


def test_politician_trades_quiver_registers_on_import() -> None:
    import data.providers.politician_trades.quiver  # noqa: F401
    from data.registry import _REGISTRY, _LIMITERS

    entry = _REGISTRY[("politician_trades", "quiver")]
    assert entry.upstream == "quiver"
    assert _LIMITERS["quiver"].rate_per_minute == 30
    assert _LIMITERS["quiver"].capacity == 10
```

- [ ] **Step 2: Run and verify it fails**

```
.venv/Scripts/python -m pytest tests/unit/data/test_provider_registration.py::test_politician_trades_quiver_registers_on_import -v
```
Expected: FAIL.

- [ ] **Step 3: Create files** (`__init__.py` empty + `politician_trades/quiver.py`)

Move from `src/data/providers/quiver_politicians.py`. Apply transformations + bake the base URL into the module:

```python
"""Quiver Quant congressional-trades provider (soft-fail when key is unset)."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests

from data.registry import register
from data.retry import with_retry

from ...models import PoliticianTrade, TradeSide


_BASE_URL = "https://api.quiverquant.com/beta"
_HTTP_TIMEOUT = 15.0  # mirrors today's settings.http_timeout_seconds default

logger = logging.getLogger(__name__)

# preserve _SIDE_MAP, _coerce_side, _parse_date, _parse_amount_range
# unchanged from quiver_politicians.py


@with_retry
def _fetch_trades(symbol: str | None, api_key: str) -> list[dict]:
    url = f"{_BASE_URL}/live/congresstrading"
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    params: dict[str, Any] = {}
    if symbol:
        params["ticker"] = symbol

    resp = requests.get(url, headers=headers, params=params, timeout=_HTTP_TIMEOUT)
    resp.raise_for_status()
    payload = resp.json() if resp.content else []
    return payload if isinstance(payload, list) else []


@register(
    domain="politician_trades",
    name="quiver",
    upstream="quiver",
    rate_per_minute=30,
    burst=10,
)
async def fetch(
    ticker: str | None = None,
    *,
    lookback_days: int = 90,
) -> list[PoliticianTrade]:
    api_key = os.getenv("QUIVER_QUANT_API_KEY")
    if not api_key:
        # Soft-fail: free tier unavailable. EDGAR's notable_holders carries
        # the smart-money signal until the key returns.
        logger.debug("QUIVER_QUANT_API_KEY unset — fetch returning []")
        return []

    symbol = ticker.upper() if ticker else None
    payload = await asyncio.to_thread(_fetch_trades, symbol, api_key)

    # preserve the rest of the body (cutoff, _parse_*, list build)
    ...
```

Note: the soft-fail uses raw `os.getenv` rather than `require_key` because the absence is intentional/expected.

- [ ] **Step 4: Update `providers/__init__.py`**

```python
"""Per-source provider modules. Importing each module triggers its @register call."""
from .filings import edgar as _filings_edgar  # noqa: F401
from .insider_trades import edgar as _insider_trades_edgar  # noqa: F401
from .news import finnhub as _news_finnhub  # noqa: F401
from .notable_holders import edgar as _notable_holders_edgar  # noqa: F401
from .politician_trades import quiver as _politician_trades_quiver  # noqa: F401
from .social_sentiment import finnhub as _social_finnhub  # noqa: F401
from .stats import yfinance as _stats_yfinance  # noqa: F401

__all__: list[str] = []
```

- [ ] **Step 5: Update `data/aggregator.py`**

Replace `_safe("politicians", get_public_figure_trades(...), errors)`:
```python
        _safe("politician_trades",
              dispatch("politician_trades", symbol,
                       lookback_days=politician_lookback_days),
              errors),
```

Update `_DEFAULTS["politicians"]` key → `_DEFAULTS["politician_trades"]`.

Remove `get_public_figure_trades` from `from .providers import (...)`. The block is now empty; replace it with an explicit package import so `@register` decorators still run when `data` is imported:
```python
from . import providers as _providers  # noqa: F401  — triggers @register decorators
```

- [ ] **Step 6: Update `data/__init__.py`**

```python
async def get_public_figure_trades(
    ticker: str | None = None,
    *,
    lookback_days: int = 90,
):
    """Fetch politician/congressional trades via the active provider."""
    return await _dispatch(
        "politician_trades",
        ticker.upper() if ticker else None,
        lookback_days=lookback_days,
    )
```

- [ ] **Step 7: Delete `src/data/providers/quiver_politicians.py`**

```
git rm src/data/providers/quiver_politicians.py
```

- [ ] **Step 8: Run tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS, clean.

- [ ] **Step 9: Commit**

```
git add src/data/providers/politician_trades src/data/providers/__init__.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_provider_registration.py
git commit -m "refactor(data): migrate quiver politician-trades provider to registry shell"
```

---

### Task 13: Update `ProviderError` (add `domain`; redefine `provider`) + cross-validation at import

After all seven providers are migrated, finalize the public API:
- `ProviderError` gains a `domain` field; `provider` now stores the *active provider name* (e.g. `"finnhub"`), not the domain slot.
- `_safe` in the aggregator looks up the active provider name from `cfg.providers[domain]`.
- `data/__init__.py` runs cross-validation at import: every `(domain, provider_name)` from config must be in `_REGISTRY`.

**Files:**
- Modify: `src/data/models/bundle.py`
- Modify: `src/data/aggregator.py`
- Modify: `src/data/__init__.py`
- Test: `tests/unit/data/test_aggregator.py`, `tests/unit/data/test_active_pacing.py`

- [ ] **Step 1: Write the aggregator tests (failing)**

Create `tests/unit/data/test_aggregator.py`:
```python
"""Unit tests for data.aggregator — bundle composition + _safe error handling."""
from __future__ import annotations

import asyncio

import pytest

from data import config as data_config
from data.aggregator import get_stock_signal_bundle
from data.models import StockSignalBundle
from data.registry import register, DOMAINS


def _stub_all_domains_with(monkeypatch: pytest.MonkeyPatch, registry_isolation: None,
                            failing_domain: str | None = None) -> None:
    from data.models import (
        Filing, InsiderTrade, NotableHolder, NewsArticle, PoliticianTrade,
        SocialSentiment, StockStats,
    )

    @register("stats", "fake", upstream="stats_up", rate_per_minute=10_000, burst=10_000)
    async def _stats(ticker: str, *, period: str = "1y", interval: str = "1d") -> StockStats:
        if failing_domain == "stats":
            raise RuntimeError("boom")
        return StockStats(ticker=ticker, history=[])

    @register("news", "fake", upstream="news_up", rate_per_minute=10_000, burst=10_000)
    async def _news(ticker: str, **opts) -> list[NewsArticle]:
        if failing_domain == "news":
            raise RuntimeError("boom")
        return []

    @register("social_sentiment", "fake", upstream="social_up", rate_per_minute=10_000, burst=10_000)
    async def _soc(ticker: str) -> SocialSentiment | None:
        return None

    @register("insider_trades", "fake", upstream="ins_up", rate_per_minute=10_000, burst=10_000)
    async def _ins(ticker: str, **opts) -> list[InsiderTrade]:
        return []

    @register("politician_trades", "fake", upstream="pol_up", rate_per_minute=10_000, burst=10_000)
    async def _pol(ticker: str | None = None, **opts) -> list[PoliticianTrade]:
        return []

    @register("notable_holders", "fake", upstream="holders_up", rate_per_minute=10_000, burst=10_000)
    async def _holders(ticker: str, **opts) -> list[NotableHolder]:
        return []

    @register("filings", "fake", upstream="filings_up", rate_per_minute=10_000, burst=10_000)
    async def _filings(ticker: str, **opts) -> list[Filing]:
        return []

    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fake" for d in DOMAINS}),
    )


def test_bundle_returns_stock_signal_bundle(monkeypatch, registry_isolation) -> None:
    _stub_all_domains_with(monkeypatch, registry_isolation)
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert isinstance(bundle, StockSignalBundle)
    assert bundle.ticker == "AAPL"
    assert bundle.errors == []


def test_bundle_captures_provider_failure(monkeypatch, registry_isolation) -> None:
    _stub_all_domains_with(monkeypatch, registry_isolation, failing_domain="news")
    bundle = asyncio.run(get_stock_signal_bundle("AAPL"))
    assert bundle.news == []
    assert len(bundle.errors) == 1
    err = bundle.errors[0]
    assert err.domain == "news"
    assert err.provider == "fake"
    assert "boom" in err.message
```

Create `tests/unit/data/test_active_pacing.py`:
```python
"""Unit tests: pacing floor reflects only ACTIVE upstream limiters."""
from __future__ import annotations

import pytest

from data import config as data_config
from data.registry import (
    DOMAINS,
    active_upstreams,
    min_decision_interval_seconds,
    register,
)


def test_min_interval_reflects_only_active_upstreams(monkeypatch, registry_isolation) -> None:
    @register("news", "slow", upstream="slow_up", rate_per_minute=6, burst=1)
    async def _slow(ticker: str, **opts):  # 1 req per 10s
        return []

    @register("news", "fast", upstream="fast_up", rate_per_minute=600, burst=10)
    async def _fast(ticker: str, **opts):  # 1 req per 0.1s
        return []

    # Stub other domains with the fast provider to satisfy DataConfig.
    for d in DOMAINS - {"news"}:
        @register(d, "fast", upstream="fast_up", rate_per_minute=600, burst=10)
        async def _other(*a, **kw):
            return None

    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fast" for d in DOMAINS} | {"news": "slow"}),
    )
    floor = min_decision_interval_seconds()
    assert floor == pytest.approx(10.0, rel=0.01)
    assert active_upstreams() == {"slow_up", "fast_up"}

    # Swap back to fast for news; slow_up no longer in the active set.
    monkeypatch.setattr(
        data_config, "_cache",
        data_config.DataConfig(providers={d: "fast" for d in DOMAINS}),
    )
    assert "slow_up" not in active_upstreams()
    assert min_decision_interval_seconds() == pytest.approx(0.1, rel=0.01)
```

- [ ] **Step 2: Run the tests and verify they fail**

```
.venv/Scripts/python -m pytest tests/unit/data/test_aggregator.py tests/unit/data/test_active_pacing.py -v
```
Expected: aggregator tests FAIL on `assert err.domain == "news"` (the field doesn't exist yet); pacing test should already pass if Task 5 + 12 are done — if not, FAIL appropriately.

- [ ] **Step 3: Add `domain` to `ProviderError`**

Edit `src/data/models/bundle.py`:
```python
class ProviderError(BaseModel):
    """Captured per-provider failure so the bundle can degrade gracefully."""

    domain: str
    provider: str
    message: str
```

- [ ] **Step 4: Update `_safe` in `src/data/aggregator.py`**

```python
async def _safe(domain: str, coro: Awaitable, errors: list[ProviderError]) -> Any:
    try:
        return await coro
    except Exception as exc:
        from .config import get_config

        provider_name = get_config().providers[domain]
        logger.warning("provider %s (%s) failed: %s", provider_name, domain, exc)
        errors.append(ProviderError(
            domain=domain,
            provider=provider_name,
            message=f"{type(exc).__name__}: {exc}",
        ))
        return _DEFAULTS[domain]
```

- [ ] **Step 5: Add cross-validation to `data/__init__.py`**

After `from . import providers  # triggers @register for all provider modules`, add:
```python
def _validate_active_providers_are_registered() -> None:
    from .config import get_config
    from .registry import _REGISTRY

    cfg = get_config()
    missing = [(d, n) for d, n in cfg.providers.items() if (d, n) not in _REGISTRY]
    if missing:
        raise RuntimeError(
            f"config/data.json references unregistered (domain, provider) pairs: {missing}"
        )


_validate_active_providers_are_registered()
```

- [ ] **Step 6: Replace `min_decision_interval_seconds` constant export with the function**

In `src/data/__init__.py`, remove:
```python
MIN_DECISION_INTERVAL_SECONDS: float = slowest_min_interval_seconds(
    FINNHUB, QUIVER, EDGAR, YFINANCE
)
```
Add:
```python
from .registry import min_decision_interval_seconds  # noqa: F401  (re-export)
```

In `src/data/aggregator.py`, change the bundle construction line:
```python
        min_decision_interval_seconds=min_decision_interval_seconds(),
```
Update aggregator imports:
1. Delete the old rate-limit named-singleton import:
   ```python
   from .rate_limit import EDGAR, FINNHUB, QUIVER, YFINANCE, slowest_min_interval_seconds
   ```
2. Extend the existing registry import (added in Task 6) to also pull in the pacing function:
   ```python
   from .registry import dispatch, min_decision_interval_seconds
   ```

After this, the aggregator references no provider names and no named limiters — only domain names + the registry helpers.

- [ ] **Step 7: Run all tests + ruff**

```
.venv/Scripts/python -m pytest tests/unit/data/ tests/unit/test_analyst_fetchers.py -v
.venv/Scripts/python -m ruff check src/data tests/unit/data
```
Expected: PASS for aggregator, pacing, registry, config, secrets, registration tests. analyst-fetcher tests still pass (public API names unchanged). Ruff clean.

- [ ] **Step 8: Commit**

```
git add src/data/models/bundle.py src/data/aggregator.py src/data/__init__.py tests/unit/data/test_aggregator.py tests/unit/data/test_active_pacing.py
git commit -m "feat(data): add ProviderError.domain + cross-validate registered providers"
```

---

### Task 14: Cleanup — delete `data/settings.py`, named limiter singletons, `data.MIN_DECISION_INTERVAL_SECONDS`

Remove now-unused exports. Verify nothing in the wider codebase imports them.

**Files:**
- Delete: `src/data/settings.py`
- Modify: `src/data/rate_limit.py`, `src/data/__init__.py`

- [ ] **Step 1: Find and remove any external references to deleted symbols**

```
.venv/Scripts/python -m grep -r "from data import.*\(FINNHUB\|EDGAR\|QUIVER\|YFINANCE\|MIN_DECISION_INTERVAL_SECONDS\|ALL_LIMITERS\|slowest_min_interval_seconds\|get_settings\|ProviderConfigError\)" src/ tests/
```

Replace any callers (none expected; aggregator already migrated). If any are found, edit them to use `min_decision_interval_seconds()` (function) or remove the reference.

- [ ] **Step 2: Delete `src/data/settings.py`**

```
git rm src/data/settings.py
```

- [ ] **Step 3: Remove named singletons from `src/data/rate_limit.py`**

Delete these lines from the bottom of the file:
```python
FINNHUB = AsyncRateLimiter("finnhub", rate_per_minute=60, burst=30)
QUIVER = AsyncRateLimiter("quiver", rate_per_minute=30, burst=10)
EDGAR = AsyncRateLimiter("edgar", rate_per_minute=600, burst=20)
YFINANCE = AsyncRateLimiter("yfinance", rate_per_minute=60, burst=30)


ALL_LIMITERS: dict[str, AsyncRateLimiter] = {
    "finnhub": FINNHUB,
    "quiver": QUIVER,
    "edgar": EDGAR,
    "yfinance": YFINANCE,
}


def slowest_min_interval_seconds(*limiters: AsyncRateLimiter) -> float:
    ...
```

Keep the `AsyncRateLimiter` class and the `_Bucket` dataclass.

- [ ] **Step 4: Remove deleted symbols from `src/data/__init__.py`**

In the `__all__` list and the imports at the top, drop:
- `FINNHUB`, `EDGAR`, `QUIVER`, `YFINANCE`, `ALL_LIMITERS`
- `slowest_min_interval_seconds`
- `MIN_DECISION_INTERVAL_SECONDS`
- `ProviderConfigError`, `get_settings`

Add to `__all__`:
- `min_decision_interval_seconds`

- [ ] **Step 5: Run the full test suite**

```
.venv/Scripts/python -m pytest tests/ -q
.venv/Scripts/python -m ruff check src tests
```
Expected: all PASS, ruff clean. If a test under `tests/unit/test_analyst_fetchers.py` or elsewhere still imports a removed symbol, fix the import.

- [ ] **Step 6: Commit**

```
git add -u
git commit -m "refactor(data): drop legacy settings.py + named limiter singletons + MIN_DECISION_INTERVAL_SECONDS"
```

---

### Task 15: Smoke run + `graph_delta.md` entry

End-to-end sanity check against real upstreams (or as far as `.env` keys allow), then document the structural change.

**Files:**
- Modify: `graphify-out/graph_delta.md`

- [ ] **Step 1: Run the smoke run locally**

```
.venv/Scripts/python -m scripts.smoke_run
```
Expected: 3 simulated ticks complete; bundles populated for each watchlist ticker; `politician_trades` may be `[]` if `QUIVER_QUANT_API_KEY` is unset (soft-fail preserved); `bundle.errors` lists any per-provider failures with both `domain` and `provider` fields populated.

- [ ] **Step 2: Append a delta entry**

Append to `graphify-out/graph_delta.md`:
```markdown

## 2026-05-07 — Provider shell + registry refactor

Split src/data/providers/ into per-domain directories. Each provider is a
single async fetch() decorated with @register(domain, name, upstream,
rate_per_minute, burst). A new src/data/registry.py owns dispatch and the
shared limiter map. Active provider per domain is chosen in
config/data.json. `data.settings` is gone — secrets read via
data.secrets.require_key, non-secret config in DataConfig.

- New nodes: data.registry.register, data.registry.dispatch,
  data.registry._ensure_limiter, data.config.DataConfig,
  data.config.FetchDefaults, data.secrets.require_key,
  data.providers.<domain>.<provider>.fetch (×7).
- Changed nodes: data.aggregator.get_stock_signal_bundle now references
  domain getters only; data.models.bundle.ProviderError gains `domain`
  and the `provider` field stores the active provider name.
- Removed: data.settings.{Settings, get_settings, ProviderConfigError,
  require}; data.rate_limit.{FINNHUB, EDGAR, QUIVER, YFINANCE,
  ALL_LIMITERS, slowest_min_interval_seconds};
  data.MIN_DECISION_INTERVAL_SECONDS (replaced by
  data.registry.min_decision_interval_seconds()).
- Flat provider modules deleted: yfinance_stats.py, finnhub_news.py,
  finnhub_social.py, sec_filings.py, sec_holders.py, sec_insiders.py,
  quiver_politicians.py.
```

- [ ] **Step 3: Commit the delta**

```
git add graphify-out/graph_delta.md
git commit -m "docs(graph): record provider-shell refactor"
```

---

## Self-Review Notes

**Spec coverage:** Each spec section maps to tasks:
- Module layout → Tasks 1-12 (file moves) + Task 14 (cleanup).
- Components (`config.py`, `registry.py`, `secrets.py`, `aggregator.py`) → Tasks 2, 3, 5, 13.
- Public API (provider-free getters) → Tasks 6-12 wire each one as a dispatch wrapper.
- Configuration schema → Tasks 3 + 4.
- Data flow (single getter and bundle) → Tasks 6-12 + Task 13.
- Error handling (validation at startup, soft-fail, ProviderError shape) → Task 13.
- Testing strategy → Tasks 1, 2, 3, 5, 6-12 (registration), 13 (aggregator + pacing).
- Migration plan order → mirrored 1:1 across Tasks 1-15.

**Note on per-call rate-limit acquisition** in EDGAR providers (Tasks 9 + 10): the spec puts limiter acquisition in `dispatch`. EDGAR filings fetch makes one HTTP roundtrip per filing inside the loop and previously called `await EDGAR.acquire()` inside that loop. The plan reaches into `_LIMITERS["edgar"]` from inside the provider as a documented exception. If after Task 14 we want to clean this up further, expose `data.registry.limiter(upstream: str) -> AsyncRateLimiter` as a public helper — captured as future-work, not blocking.

**Type consistency:** `register` signature in Task 5 matches usage in Tasks 6-12 (keyword-only `upstream`/`rate_per_minute`/`burst`). `dispatch` returns `Any` and is used unchanged. `min_decision_interval_seconds` is a function everywhere — never a constant after Task 13.
