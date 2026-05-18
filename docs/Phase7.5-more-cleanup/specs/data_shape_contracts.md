# Data-Shape Contracts at Layer Boundaries — Spec

## Status

- **Drafted:** 2026-05-18
- **Origin:** `docs/todo-fixes.md` items 2.1, 2.2, 2.3
- **Context:** pre-deployment refactor; no production rollout concerns

## Goal

Pin the data shape at every layer boundary between providers, agents, and the
cache, so that:

1. Live and cache implementations of the same provider domain return identical
   types.
2. Per-ticker state structures use a consistent ticker-first convention.
3. Dead aggregation surfaces stop pretending to be useful.

The unifying weakness behind all three items is that **data shapes at layer
boundaries aren't pinned**.  The registry stores
`Callable[..., Awaitable[Any]]`, so live and cache providers can silently
drift.  Per-ticker state uses ad-hoc nested dicts.  Aggregator helpers exist
that nothing calls.

## Motivation

From `docs/todo-fixes.md`:

- **2.1 Provider return-type unification** — `src/data/registry.py` stores
  providers as `Callable[..., Awaitable[Any]]` with no return-type
  enforcement.  At least one drift case already exists:
  `src/backtest/providers/insider_trades_cache.py:14-16` wraps results in
  `Form4Bundle(...)`, while the live provider returns a
  `tuple[list[InsiderTrade], list[InsiderDerivativeTransaction]]`.

- **2.2 Smart-money state shape** — `state["smart_money_data"]` is
  category-first today
  (`{"politicians": {ticker: [...]}, "notable_holders": {ticker: [...]}}`),
  making per-ticker slicing awkward and bypass-prone.  Contrary to what
  `docs/todo-fixes.md` 2.2 implies, **no `SmartMoneyRaw` Pydantic model
  currently exists** — Group 2 introduces it.

- **2.3 `aggregator.get_stock_signal_bundle`** — present in
  `src/data/aggregator.py` along with `StockSignalBundle`.  Zero production
  callers (grep-confirmed across `src/agents/`, `src/orchestrator/`,
  `src/backtest/`).  Three test files exercise the function; nothing else.

## Scope

### In scope

- Define `Provider[T]` Protocol and a `DOMAIN_SHAPES` constant in
  `src/data/registry.py`.
- Audit all 12 active provider domains; produce a canonical-shape table.
- Align live and cache implementations against the canonical shape for each
  domain.
- Introduce `SmartMoneyRaw` Pydantic model.
- Reshape `state["smart_money_data"]` to ticker-first at fetch time.
- Delete `get_stock_signal_bundle`, `get_stock_signal_bundle_blocking`,
  `StockSignalBundle`, `src/data/aggregator.py`, and the three orphaned tests.
- Land a parametrised contract test enforcing the shapes going forward.

### Out of scope

- **PIT correctness / leak audit** — deferred to a separate spec (already on
  the backlog as `backtest-pit-correctness-v1`).
- **Per-window cache compartmentalisation** — deferred.
- **API fidelity** — checking that our models accurately reflect each
  upstream API's documented shape is a different concern.  Group 2 is about
  *internal* consistency between live and cache, not external accuracy.
- **`Form4Bundle` composition** — already a legitimate
  "genuinely-multiple-sublists" case; left as-is.
- **Adding new domains** or filling cache backfills.
- **Registry plumbing beyond adding `DOMAIN_SHAPES`** — the registry's
  internal storage stays `Callable[..., Awaitable[Any]]`.

## Architecture

All three items live in one spec because they share the same root cause and
the same fix-surface (providers + cache + per-ticker state).  Splitting them
would mean three separate audits of the same code paths.

Execution is sliced into five phases:

- **Phase A — Foundations:** audit document, `Provider[T]` Protocol,
  `DOMAIN_SHAPES` constant, `SmartMoneyRaw` model, contract-test framework
  (with `xfail` rows for domains not yet aligned).
- **Phase B — Per-domain alignment:** 12 commits, one per domain.  Each pins
  the canonical shape (single, list, or bundle), aligns live + cache, and
  removes the relevant contract-test `xfail`.
- **Phase C — Smart-money reshape:** fetch-site rewrite + agent slicing-path
  update + smart-money unit tests.
- **Phase D — Aggregator delete:** remove `src/data/aggregator.py`, the three
  orphaned test files, and the `__init__.py` re-exports.
- **Phase E — Final sweep:** verify zero `xfail` markers in
  `test_provider_shapes.py`, zero `get_stock_signal_bundle` references in
  the tree, update docs, write `done.md`.

Roughly 16–17 commits total.

## Components

### 1. `Provider[T]` Protocol + `DOMAIN_SHAPES` constant

**Location:** `src/data/registry.py` (additive — does not touch existing
dispatch logic).

```python
from dataclasses import dataclass
from typing import Awaitable, Literal, Protocol, TypeVar

T = TypeVar("T")


class Provider(Protocol[T]):
    """A registered data provider — an async callable returning a
    domain-canonical shape.  T is the canonical type pinned in
    DOMAIN_SHAPES for the provider's domain."""

    async def __call__(self, *args, **kwargs) -> T: ...


@dataclass(frozen=True)
class DomainShape:
    """Canonical return-shape for a registered provider domain.

    container:
      - "single"  one Pydantic model instance
      - "list"    list[payload_type]
      - "bundle"  a wrapper model with multiple sublists (e.g. Form4Bundle)

    payload_type: the element model class (for "list") or the model class
    itself (for "single" / "bundle").
    """

    container: Literal["single", "list", "bundle"]
    payload_type: type


# Populated from audit findings.  Every entry is the canonical shape that
# both live and cache providers must return for this domain.
DOMAIN_SHAPES: dict[str, DomainShape] = {
    # 12 entries — concrete values land with the Phase A audit commit.
}
```

The registry's internal storage continues to be
`Callable[..., Awaitable[Any]]`.  The Protocol and constant are
*documentation + a hook the contract test consumes*; they don't enforce type
safety at dispatch.  This is intentional — the value comes from the
behavioural contract test, not from static typing of `Any`-erased
callables.

### 2. Audit document

**Location:** `docs/Phase7.5-more-cleanup/audit/provider_shapes.md`.

Single markdown table; one row per active domain.  Columns:

| Domain | Live provider | Cache provider | Live current shape | Cache current shape | Match? | Canonical shape | Notes |

Landed as the first commit in Phase A.  Drives every entry in
`DOMAIN_SHAPES`.  Stays in the repo as a historical record of what was
discovered and what was decided.

**Shape-picking principle:**
- One thing → `single` (e.g. `CompanyRatios`, `ShortInterest`).
- Many of the same thing → `list[Model]` (e.g. `list[OHLCBar]`,
  `list[NewsItem]`).
- Genuinely multiple sublists with no single natural payload type →
  `bundle` (`Form4Bundle` is the only known case today).

### 3. `SmartMoneyRaw` Pydantic model

**Location:** `src/data/models/smart_money.py` (new file, sibling to the
other per-domain model files).

```python
from pydantic import BaseModel, ConfigDict, Field

from src.data.models.politician_trade import PoliticianTrade
from src.data.models.notable_holder import NotableHolder


class SmartMoneyRaw(BaseModel):
    """Per-ticker smart-money payload — politicians + notable holders for
    a single ticker.  Used as the value type in
    state['smart_money_data'][ticker] after Phase C."""

    model_config = ConfigDict(extra="forbid")

    politicians: list[PoliticianTrade] = Field(default_factory=list)
    notable_holders: list[NotableHolder] = Field(default_factory=list)
```

Exact import paths and model-class names confirmed during Phase A against
the current `src/data/models/` layout.

`SmartMoneyRaw` is **agent-internal** — it does not enter `DOMAIN_SHAPES`.
The underlying provider domains (`politician_trades`, `notable_holders`)
keep their own canonical shapes; `SmartMoneyRaw` is the smart-money agent's
private aggregation type.

### 4. Contract test

**Location:** `tests/contract/test_provider_shapes.py` (new directory).

```python
import pytest
from src.data.registry import DOMAIN_SHAPES


@pytest.mark.contract
@pytest.mark.parametrize("domain", sorted(DOMAIN_SHAPES.keys()))
async def test_live_and_cache_providers_match_canonical_shape(
    domain: str,
) -> None:
    """For every registered domain, both the live and cache implementations
    return an object matching DOMAIN_SHAPES[domain]."""

    shape = DOMAIN_SHAPES[domain]

    live = await _call_live_provider(domain, mocked_http=True)
    cache = await _call_cache_provider(domain, in_memory_store=True)

    assert _matches_shape(live, shape), (
        f"live provider for {domain!r} returned {type(live).__name__}, "
        f"expected {shape}"
    )
    assert _matches_shape(cache, shape), (
        f"cache provider for {domain!r} returned {type(cache).__name__}, "
        f"expected {shape}"
    )
```

`_matches_shape(value, shape)` rules:
- `container == "single"` → `isinstance(value, shape.payload_type)`
- `container == "bundle"` → `isinstance(value, shape.payload_type)`
- `container == "list"` → `isinstance(value, list)` and every element
  `isinstance(_, shape.payload_type)`

A new pytest marker `@pytest.mark.contract` is registered in `pyproject.toml`
(or wherever the existing pytest config lives) and included in the default
pytest run.

### 5. Smart-money rewiring

**Files modified:**

- `src/agents/analysts/smart_money/fetch.py:81-110` — writer rewritten to
  produce per-ticker `SmartMoneyRaw` instances.
- The smart-money agent's slicing / prompt-formatting code — updated to
  consume `state["smart_money_data"][ticker]` as a `SmartMoneyRaw` and
  access `.politicians` / `.notable_holders`.

**State shape (visible in `state["smart_money_data"]`):**

```python
# Before
state["smart_money_data"] = {
    "politicians":     {"AAPL": [...], "MSFT": [...]},
    "notable_holders": {"AAPL": [...], "MSFT": [...]},
}

# After
state["smart_money_data"] = {
    "AAPL": SmartMoneyRaw(politicians=[...], notable_holders=[...]),
    "MSFT": SmartMoneyRaw(politicians=[...], notable_holders=[...]),
}
```

### 6. Aggregator deletion

**Files deleted:**

- `src/data/aggregator.py` (whole file)
- Three orphaned test files exercising the aggregator (exact list captured
  in the Phase A audit step)

**Files edited:**

- `src/data/__init__.py` — remove `get_stock_signal_bundle`,
  `get_stock_signal_bundle_blocking`, `StockSignalBundle` from `__all__`
  and any re-exports.

**Sweep verification (must return zero hits before Phase D commits):**

```bash
grep -rn "get_stock_signal_bundle\|StockSignalBundle" src/ tests/
```

## Data flow

### Provider call

No structural change.  Live or backtest, the call shape is identical:

```
Agent.fetch_step
    └── data.get_<domain>(ticker, ...)
            └── registry.dispatch("<domain>", provider_name)
                    └── live OR cache provider returns DOMAIN_SHAPES["<domain>"]
```

What changes after Group 2: the returned object is *guaranteed* to match
`DOMAIN_SHAPES[domain]`.  The contract test enforces it on every PR.

### Smart-money state

See Components §5 for the before/after shape.  This is the only data-flow
change in Group 2.

### Aggregator deletion

No flow change.  `get_stock_signal_bundle` has zero production callers
(grep-confirmed in `src/agents/`, `src/orchestrator/`, `src/backtest/`).
Removing the function and its bundle type does not require any
orchestrator or agent rewiring.

## Error handling

Group 2 introduces **no new runtime error paths**.  Safety strategy is
*fail at CI*, not fail at runtime.

**Build-time / CI gates:**
- Contract test catches drift between live and cache implementations on
  every PR.
- `extra="forbid"` on `SmartMoneyRaw` rejects unexpected fields at
  construction — fails loud at fetch if a writer is wrong.
- Final-sweep grep catches any aggregator imports that survived the
  Phase D delete.

**Runtime — unchanged:**
- Per-provider error handling (network, parse, rate-limit, missing key)
  flows through existing paths.  Group 2 doesn't touch them.
- The smart-money fetch site lets a `ValidationError` propagate when
  constructing `SmartMoneyRaw` with malformed inputs.  The smart-money
  agent already short-circuits any ticker whose data is missing or
  invalid — the new shape inherits that behaviour without new code.

## Testing

### New tests

- `tests/contract/test_provider_shapes.py` — parametrised over
  `DOMAIN_SHAPES`.  Marker `@pytest.mark.contract`, included in default
  pytest run.

### Sequencing the contract test

Contract test lands at the end of Phase A with all 12 rows present.
Domains not yet aligned at that point are marked:

```python
pytest.mark.xfail(strict=True, reason="domain not yet aligned — see Phase B task <n>")
```

Each Phase B commit removes one `xfail`.  Phase E sweep verifies zero
`xfail` markers remain in `test_provider_shapes.py`.

Mirrors the pattern Group 1 used — the gap is visible from day one, and
each commit measurably closes one row.

### Existing tests touched

- Smart-money unit tests — fixtures and slicing updates (~3–4 files).
- Per-domain provider unit tests where the audit reveals shape drift
  (e.g. `insider_trades` live tuple → `Form4Bundle`).
- End-to-end backtest smoke test
  (`tests/integration/backtest/test_end_to_end_smoke.py`) — runs
  unchanged; behavioural regression gate after each Phase B commit.

### Removed tests

- Three test files exercising the aggregator (exact list captured during
  the Phase A audit step).

### Acceptance per phase

- **Phase B per-domain commits:** full pytest + smoke test pass; the
  contract-test row for that domain goes from `xfail` to passing.
- **Phase C:** smart-money tests + smoke test pass.
- **Phase D:** aggregator imports removed; pytest collection succeeds
  with no missing imports.
- **Phase E:** clean pytest run; zero `xfail` markers in
  `test_provider_shapes.py`; all greps clean.

## Phase folder artefacts

```
docs/Phase7.5-more-cleanup/
├── specs/
│   └── data_shape_contracts.md          # this file
├── audit/
│   └── provider_shapes.md               # written in Phase A
├── plans/
│   └── data_shape_contracts_v1.md       # written by writing-plans skill next
└── done.md                              # written at end of Phase E
```

## References

- `docs/todo-fixes.md` — items 2.1, 2.2, 2.3
- `docs/Phase7.5-more-cleanup/specs/config_as_truth.md` — same
  Phase-folder pattern (Phase 7.5 and Phase 7.6 share this folder)
- `src/data/registry.py` — current registry, target of `Provider[T]` and
  `DOMAIN_SHAPES` additions
- `src/data/aggregator.py` — to be deleted
- `src/agents/analysts/smart_money/fetch.py:81-110` — reshape target
- `src/backtest/providers/insider_trades_cache.py:14-16` — example of
  pre-existing drift
