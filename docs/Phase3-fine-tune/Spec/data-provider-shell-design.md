# Data Provider Shell — Design Spec

## Summary

Refactor `src/data/` so the agent-facing layer is fully provider-agnostic.
A single registry maps each data **domain** (news, stats, filings, …) to
exactly one **active provider** chosen in `config/data.json`. Adding or
swapping a provider is a one-file drop plus a one-line config change — no
agent, aggregator, or callsite edits.

The public API stays domain-shaped (`get_stock_news`, `get_stock_stats`, …)
with uniform `(ticker, **opts) -> DomainModel` signatures, so the same
functions can be wrapped as ADK `FunctionTool`s later when agents move from
pre-fetched-each-tick to on-demand data tools.

## Goals

- **Provider-free agent code.** No agent, aggregator, or analyst-callback
  imports a provider module or rate-limit singleton by name.
- **Swap providers without rewiring.** Change the active provider for a
  domain by editing one JSON value. Files that do or don't reference the
  swapped provider are untouched.
- **Each provider is a "shell"** — one async function per `(domain, name)`
  that gathers its own secret, calls upstream, and maps the response into
  the domain's Pydantic model. Rate limiting, retry, and dispatch live
  outside the provider.
- **Config is the exhaustive source of truth for the data layer.** Active
  providers, fetch defaults, and HTTP timeout live in `config/data.json`.
  Secrets stay in `.env`. Upstream facts (rate caps, base URLs) live with
  the provider that knows them.
- **Future-proof for ADK Tools.** Each public getter is an async typed
  function with a stable signature; wrapping it as an LLM-callable tool is
  a one-liner with no internal change.

## Non-goals

- Multi-provider fallback or merging within a single domain. Single active
  provider per domain only. Adding fallback chains is a possible future
  extension; out of scope here.
- Migrating other subsystems' configuration (risk gate clamps, memory
  buffer thresholds, Gemini model names). Data layer only.
- Changing domain shape or splitting `StockStats` into separate
  history/fundamentals slots. The seven existing domains stay as-is.
- Changing what providers exist today. The migration is structural — same
  seven providers (yfinance, Finnhub, Quiver, EDGAR×3) move into the new
  shell.

## Current state

What's already good:

- `data.aggregator.get_stock_signal_bundle()` is documented as the single
  agent-facing door, returning a normalized `StockSignalBundle` of Pydantic
  models.
- Seven thin async provider modules in `src/data/providers/`, each handling
  its own secret, rate limit, retry, and model construction.
- `_safe()` wrapper captures per-provider failures into `bundle.errors`
  without blowing up the bundle.

Where provider names still leak into agent-visible surface:

1. **Per-domain getters are exported and used directly.** All four analyst
   `fetch.py` callbacks import `get_stock_stats`, `get_stock_news`, etc.
   from `data` and bypass the bundle. The names are domain-shaped already,
   but the exports also include direct references to provider behavior.
2. **Rate-limit singletons named after providers** — `FINNHUB`, `EDGAR`,
   `QUIVER`, `YFINANCE` — are re-exported from `data/__init__.py` and
   referenced by name in the aggregator.
3. **No abstraction maps "domain need → which provider satisfies it"**.
   The aggregator hard-codes which provider fills each slot; swapping
   means editing the aggregator.
4. **`data/settings.py` mixes secrets with non-secret config**
   (HTTP timeout, Quiver base URL).
5. **`ProviderError.provider` stores the domain slot name** (`"stats"`,
   `"news"`), not the actual upstream — confusing field name for the data
   it carries.

## Architecture

### Module layout

```
src/data/
├── __init__.py              # PUBLIC API: get_<domain>(ticker, **opts), get_stock_signal_bundle()
├── config.py                # Pydantic DataConfig + loader (config/data.json)
├── registry.py              # @register decorator + dispatch + _ensure_limiter
├── secrets.py               # require_key() — thin .env reader for API keys
├── rate_limit.py            # AsyncRateLimiter class (singletons removed)
├── retry.py                 # with_retry (unchanged)
├── aggregator.py            # get_stock_signal_bundle — domain-only references
├── models/                  # Domain-shaped Pydantic models (unchanged)
└── providers/
    ├── __init__.py          # Explicit imports trigger @register at package load
    ├── stats/yfinance.py
    ├── news/finnhub.py
    ├── social_sentiment/finnhub.py
    ├── insider_trades/edgar.py
    ├── politician_trades/quiver.py
    ├── notable_holders/edgar.py
    └── filings/edgar.py
```

Provider files move from flat (`finnhub_news.py`) to nested
(`providers/news/finnhub.py`) so each domain is a directory and adding a
new news source is `providers/news/quiver.py` — one file, no rewiring
elsewhere.

### Domains

Seven, fixed at refactor time, declared as a frozen set in `registry.py`:

```python
DOMAINS = frozenset({
    "stats",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
})
```

## Components

### `data/config.py` — typed config

```python
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
    providers: dict[str, str]      # domain -> active provider name
    defaults: FetchDefaults
    http_timeout_seconds: float = 15.0

    @model_validator(mode="after")
    def _check_domains(self) -> "DataConfig":
        unknown = set(self.providers) - DOMAINS
        if unknown:
            raise ValueError(f"unknown domain(s) in config: {sorted(unknown)}")
        missing = DOMAINS - set(self.providers)
        if missing:
            raise ValueError(f"no provider configured for domain(s): {sorted(missing)}")
        return self

def get_config() -> DataConfig: ...   # cached load from config/data.json
```

A second cross-validation runs **after** providers are imported (so the
registry is populated): every `(domain, provider_name)` in
`config.providers` must have an `_REGISTRY` entry. This check lives in
`data/__init__.py` after the `from . import providers` import.

### `data/registry.py` — the shell

```python
@dataclass(frozen=True)
class _Entry:
    domain: str
    name: str
    upstream: str
    fn: Callable[..., Awaitable[Any]]

_REGISTRY: dict[tuple[str, str], _Entry] = {}
_LIMITERS: dict[str, AsyncRateLimiter] = {}

def register(
    domain: str,
    name: str,
    *,
    upstream: str,
    rate_per_minute: float,
    burst: int,
):
    def deco(fn):
        if domain not in DOMAINS:
            raise ValueError(f"unknown domain: {domain}")
        _ensure_limiter(upstream, rate_per_minute, burst)
        _REGISTRY[(domain, name)] = _Entry(domain, name, upstream, fn)
        return fn
    return deco

def _ensure_limiter(upstream: str, rpm: float, burst: int) -> AsyncRateLimiter:
    if upstream in _LIMITERS:
        existing = _LIMITERS[upstream]
        if (existing.rate_per_minute, existing.capacity) != (rpm, burst):
            raise ValueError(
                f"conflicting rate-limit declarations for upstream {upstream!r}: "
                f"already {existing.rate_per_minute}/min burst {existing.capacity}, "
                f"got {rpm}/min burst {burst}"
            )
        return existing
    lim = AsyncRateLimiter(upstream, rpm, burst)
    _LIMITERS[upstream] = lim
    return lim

async def dispatch(domain: str, *args, **kwargs):
    cfg = get_config()
    name = cfg.providers[domain]
    entry = _REGISTRY[(domain, name)]
    await _LIMITERS[entry.upstream].acquire()
    return await entry.fn(*args, **kwargs)
```

`dispatch` owns rate-limit acquisition. Providers do **not** call
`limiter.acquire()` — this prevents drift and centralizes the policy.

### Active-upstream pacing floor

```python
def active_upstreams() -> set[str]:
    cfg = get_config()
    return {_REGISTRY[(d, n)].upstream for d, n in cfg.providers.items()}

def min_decision_interval_seconds() -> float:
    return max(
        (_LIMITERS[u].min_interval_seconds for u in active_upstreams()),
        default=0.0,
    )
```

Swap a provider out → its limiter is no longer in `active_upstreams()` →
the pacing floor recomputes automatically. Used by
`StockSignalBundle.min_decision_interval_seconds` so the strategist's
trading cadence guard adapts to the active provider set.

### `data/secrets.py` — `.env` reader

Replaces the secret-loading parts of the deleted `data/settings.py`.

```python
class SecretMissingError(RuntimeError):
    pass

def require_key(env_var: str) -> str:
    """Return the env var or raise SecretMissingError."""
```

Keys are read at first call, not at import — keeps providers importable
without all keys present (matches today's behavior).

### `data/__init__.py` — public API

```python
from . import providers   # triggers @register decorators
from .registry import dispatch
from .config import get_config
get_config()               # validate cross-references at import

async def get_stock_stats(ticker: str, *, period: str | None = None,
                          interval: str | None = None) -> StockStats:
    d = get_config().defaults
    return await dispatch(
        "stats", ticker.upper(),
        period=period or d.history_period,
        interval=interval or d.history_interval,
    )

async def get_stock_news(ticker: str, *, lookback_days: int | None = None,
                         limit: int | None = 50) -> list[NewsArticle]:
    d = get_config().defaults
    today = date.today()
    days = lookback_days if lookback_days is not None else d.news_lookback_days
    return await dispatch(
        "news", ticker.upper(),
        from_date=today - timedelta(days=days),
        to_date=today,
        limit=limit,
    )

# … one thin getter per domain. Each pulls its defaults from cfg.defaults.
```

The getters are the only thing agent code imports from `data`. Provider
modules are internal.

### Provider module template

```python
# src/data/providers/news/finnhub.py
from data.registry import register
from data.secrets import require_key
from data.retry import with_retry

@register(
    domain="news", name="finnhub",
    upstream="finnhub", rate_per_minute=60, burst=30,
)
@with_retry
async def fetch(ticker: str, *, from_date, to_date, limit=50) -> list[NewsArticle]:
    api_key = require_key("FINNHUB_API_KEY")
    # … upstream call, model construction …
    return articles
```

EDGAR-backed providers (`filings`, `notable_holders`, `insider_trades`) all
declare `upstream="edgar", rate_per_minute=600, burst=20`. The first one to
register creates the limiter; the others must match — `_ensure_limiter`
catches drift between sibling files.

### `data/aggregator.py` — bundle composer

```python
async def get_stock_signal_bundle(ticker: str) -> StockSignalBundle:
    symbol = ticker.upper()
    errors: list[ProviderError] = []
    stats, news, social, insiders, politicians, holders, filings = (
        await asyncio.gather(
            _safe("stats", get_stock_stats(symbol), errors),
            _safe("news", get_stock_news(symbol), errors),
            _safe("social_sentiment", get_social_sentiment(symbol), errors),
            _safe("insider_trades", get_insider_trades(symbol), errors),
            _safe("politician_trades", get_public_figure_trades(symbol), errors),
            _safe("notable_holders", get_notable_holders(symbol), errors),
            _safe("filings", get_company_filings(symbol), errors),
        )
    )
    return StockSignalBundle(
        ticker=symbol,
        generated_at=datetime.now(tz=UTC),
        stats=stats, news=news, social_sentiment=social,
        insider_trades=insiders, politician_trades=politicians,
        notable_holders=holders, filings=filings,
        min_decision_interval_seconds=min_decision_interval_seconds(),
        errors=errors,
    )
```

Zero provider names. Reads only domain getters and the active-upstream
pacing floor.

### `_safe` and `ProviderError`

`ProviderError` gains a `domain` field and `provider` becomes the active
provider name (today it stores the domain slot — that's a documented
behavior change).

```python
class ProviderError(BaseModel):
    domain: str       # e.g. "news"
    provider: str     # e.g. "finnhub" — the active provider that failed
    message: str

async def _safe(domain: str, coro, errors):
    try:
        return await coro
    except Exception as exc:
        cfg = get_config()
        provider_name = cfg.providers[domain]
        errors.append(ProviderError(
            domain=domain,
            provider=provider_name,
            message=f"{type(exc).__name__}: {exc}",
        ))
        return _DEFAULTS[domain]
```

## Public API

After the refactor, agent and CLI code touches only the names below:

```python
from data import (
    # Domain getters (unchanged names; still domain-shaped)
    get_stock_stats,
    get_stock_news,
    get_social_sentiment,
    get_insider_trades,
    get_public_figure_trades,
    get_notable_holders,
    get_company_filings,
    # Composed bundle
    get_stock_signal_bundle,
    get_stock_signal_bundle_blocking,
    # Models (unchanged)
    StockSignalBundle, StockStats, NewsArticle, ...,
    # Pacing floor (function, not constant — depends on active providers)
    min_decision_interval_seconds,
)
```

**Removed from public surface:**

- `FINNHUB`, `EDGAR`, `QUIVER`, `YFINANCE` named limiters.
- `ALL_LIMITERS`, `slowest_min_interval_seconds`.
- `MIN_DECISION_INTERVAL_SECONDS` (constant) → replaced by
  `min_decision_interval_seconds()` (function reflecting active set).
- `get_settings`, `ProviderConfigError` — `data/settings.py` is deleted.

## Configuration schema

`config/data.json` (sits next to existing `config/watchlist.json`):

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

**What's NOT in config:**

- Rate-limit budgets — declared by each provider at `@register` time
  (upstream fact, not deployment choice).
- Provider base URLs — baked into the provider module (e.g.
  `quiver_base_url` lives in `providers/politician_trades/quiver.py`).
- Secrets — stay in `.env`, read via `data.secrets.require_key`.

**Validation** (run at import, after providers are loaded):

1. Every key in `providers` is in `DOMAINS`.
2. Every domain in `DOMAINS` has a `providers` entry (no missing slots).
3. Every `(domain, provider_name)` from `providers` exists in `_REGISTRY`.

Any failure raises at import time. No silent fallback.

## Data flow

### Single getter (e.g. analyst pre-fetch callback)

```
agent code
  └─ await data.get_stock_stats("AAPL")
       └─ dispatch("stats", "AAPL")
            ├─ cfg.providers["stats"]                  → "yfinance"
            ├─ _REGISTRY[("stats", "yfinance")]        → Entry(fn, upstream="yfinance")
            ├─ await _LIMITERS["yfinance"].acquire()
            └─ await entry.fn("AAPL", **opts)
                 └─ require_key(...) → upstream call → StockStats
```

### Bundle composition

```
get_stock_signal_bundle("AAPL")
  └─ asyncio.gather(_safe(domain, get_<domain>(...)) for domain in DOMAINS)
  → StockSignalBundle(...,
       min_decision_interval_seconds=min_decision_interval_seconds())
```

### Swapping a provider

1. Add `src/data/providers/stats/alpha_vantage.py` with
   `@register("stats", "alpha_vantage", upstream="alpha_vantage", ...)`.
2. Add one import line to `src/data/providers/__init__.py`.
3. Edit `config/data.json`: `"stats": "alpha_vantage"`.
4. Add the new secret to `.env` if needed.

No agent, aggregator, callback, or test edits required.

## Error handling

| Failure | When | Behavior |
|---|---|---|
| `config/data.json` missing/malformed | startup (import) | `ValidationError`, refuse to import `data` |
| Domain in config has no entry / unknown domain | startup | `ValidationError`, listing the offender |
| `(domain, name)` not in registry | startup | `RuntimeError` after providers imported |
| Conflicting upstream limits across providers | startup | `ValueError` from `_ensure_limiter` |
| Required secret missing for active provider | first dispatch call to that provider | `SecretMissingError`; `_safe` wraps into `bundle.errors[]` |
| Upstream API failure | per-call | `@with_retry` retries; if exhausted, raises; `_safe` captures into `bundle.errors[]` |
| Quiver-style soft-fail (no key, optional provider) | first dispatch | provider returns `[]` itself (preserves today's politician_trades behavior) |

**Loud at startup; degrade gracefully at call time.** A missing key for an
active provider does not crash the tick — it crashes only that provider's
slot, which `_safe` captures.

## Testing strategy

| Test target | What it covers |
|---|---|
| `tests/unit/data/test_registry.py` | `@register` populates `_REGISTRY`; conflicting upstream limits raise; `dispatch` picks the configured provider; unknown domain at register raises |
| `tests/unit/data/test_config.py` | Valid `config/data.json` loads; missing domain key fails validation; unregistered provider name fails validation; malformed JSON fails at load time |
| `tests/unit/data/test_active_pacing.py` | `min_decision_interval_seconds()` reflects only active upstreams; swapping a provider in test config changes the floor |
| `tests/unit/data/test_aggregator.py` | Bundle composes via public getters; `_safe` captures `(domain, provider, message)` into `bundle.errors`; partial-failure scenario produces correct slot defaults |
| Existing per-provider tests | Per-provider tests stay; they assert the provider's `fetch` returns the correct domain model. They run with the registry as-is. |

**Test-time fake providers:**

```python
@register("news", "fake_news",
          upstream="fake_news", rate_per_minute=10_000, burst=10_000)
async def fake_news_fetch(ticker, **opts):
    return [NewsArticle(...)]

def test_news_dispatch_uses_active(monkeypatch):
    monkeypatch.setattr("data.config._cached", make_test_config(
        providers={"news": "fake_news", ...}))
    result = asyncio.run(get_stock_news("AAPL"))
    assert result == [...]
```

A unique `upstream` name on the fake avoids colliding with real providers'
limiters at registration time.

## Migration plan

Single coherent refactor; tests stay green at every step.

1. **Add infrastructure** (no behavior change yet):
   - `data/config.py` + `config/data.json` (mirrors today's hardcoded
     values exactly).
   - `data/registry.py` (`@register`, `dispatch`, `_ensure_limiter`,
     `active_upstreams`, `min_decision_interval_seconds`).
   - `data/secrets.py` (`require_key`).

2. **Expose `AsyncRateLimiter.capacity`** as a public property on the
   class (currently lives on the private `_bucket` dataclass). Needed by
   `_ensure_limiter` to validate that sibling providers declared
   matching limits.

3. **Migrate providers one domain at a time.** For each:
   - Move `providers/<service>_<domain>.py` →
     `providers/<domain>/<service>.py`.
   - Add `@register(domain, name, upstream, rate_per_minute, burst)` to
     the `fetch` function. Function name becomes `fetch` (was
     `get_<domain>`).
   - Drop in-provider `await LIMITER.acquire()` calls.
   - Update `providers/__init__.py` imports.
   - Per-domain tests stay green (provider behavior unchanged).

4. **Wire the public getters** in `data/__init__.py`:
   - `get_stock_stats`, `get_stock_news`, … become 1-line wrappers around
     `dispatch(...)`.
   - Aggregator switches to importing public getters (drops direct
     provider imports, drops `slowest_min_interval_seconds` for
     `min_decision_interval_seconds`).
   - Add `domain` field + change `provider` field semantics on
     `ProviderError`.

5. **Cleanup:**
   - Delete `data/settings.py`.
   - Delete named limiter singletons (`FINNHUB`, `EDGAR`, `QUIVER`,
     `YFINANCE`) and `slowest_min_interval_seconds`,
     `MIN_DECISION_INTERVAL_SECONDS`, `ALL_LIMITERS` exports.
   - Remove direct provider exports from `data/__init__.py`.

6. **Smoke run** (`python -m scripts.smoke_run`) to confirm a full tick
   still works against real upstreams.

7. **Append `graphify-out/graph_delta.md` entry** documenting the new
   module layout, registry node, config node, and removed
   settings/rate_limit singletons.

## Future work (out of scope here)

- ADK Tool wrappers around the public getters so analysts can fetch data
  on demand mid-reasoning, not just at the start of each tick.
- Per-provider override block in config for unusual overrides
  (e.g. mock base URL during integration testing).
- Multi-provider fallback chains per domain.
- Migrating risk-gate clamps, memory thresholds, and Gemini model names
  into a unified project config (separate spec).
