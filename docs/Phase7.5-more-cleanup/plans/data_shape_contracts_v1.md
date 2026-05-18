# Phase 7.6 — Data-shape contracts implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` (recommended) or
> `superpowers:executing-plans` to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [`../specs/data_shape_contracts.md`](../specs/data_shape_contracts.md)

**Goal:** Pin the canonical data shape at every layer boundary between data
providers, agents, and the cache, so that live and cache implementations of
the same domain are guaranteed to return identical types, per-ticker state
uses a single ticker-first convention, and dead aggregation surfaces are
removed.

**Architecture:** Five phases —
(A) foundations: audit + `Provider[T]` Protocol + `DOMAIN_SHAPES` constant +
`SmartMoneyRaw` model + parametrised contract test with `xfail` rows for
drifted domains;
(B) per-domain alignment: 12 commits, one per provider domain, each
removing one `xfail`;
(C) smart-money reshape: fetch site + agent slicing + tests in one commit;
(D) aggregator delete: `src/data/aggregator.py` + three orphaned tests +
`__init__.py` re-exports gone;
(E) final sweep: grep verification + full pytest + `done.md`.

**Tech stack:** Python 3.12, Pydantic v2 (`ConfigDict(extra="forbid")`),
pytest (`asyncio_mode=auto`, new `contract` marker), SQLAlchemy
(in-memory SQLite store for the cache half of the contract test).

**Sequencing relative to Phase 7.5:** Phase 7.5 lands first.  Phase 7.6
amends Phase 7.5's aggregator-related work post-hoc (separate follow-up).
The audit task captures whatever state is current at start of Phase 7.6.

---

## Phase A — Foundations

### Task 1: Audit live and cache provider shapes

Read each live provider and each cache provider; record the current return
type for every active domain.  No code changes — only a markdown document.

**Files:**
- Create: `docs/Phase7.5-more-cleanup/audit/provider_shapes.md`

**Active domains (12):** `price_history`, `company_ratios`, `news`,
`social_sentiment`, `insider_trades`, `politician_trades`,
`notable_holders`, `filings`, `earnings`, `analyst_consensus`,
`short_interest`, `options`.

**Cache providers known to exist (8):** `price_history_cache.py`,
`company_ratios_cache.py`, `news_cache.py`, `social_sentiment_cache.py`,
`insider_trades_cache.py`, `politician_trades_cache.py`,
`notable_holders_cache.py`, `filings_cache.py`.  The remaining four
domains (`earnings`, `analyst_consensus`, `short_interest`, `options`)
have no cache provider today — record this in the audit row as
"live-only" and verify against `src/backtest/providers/`.

- [ ] **Step 1: Inspect each live provider**

For each domain `D` in the 12-domain list, open the corresponding live
provider directory under `src/data/providers/<D>/` (or top-level
`<D>.py` for the few that aren't directories — e.g. some live providers
are single files).  Record:
- File path containing the registered entry point
- The entry-point function's declared return annotation (verbatim)

- [ ] **Step 2: Inspect each cache provider**

For each domain `D` with a cache provider, open
`src/backtest/providers/<D>_cache.py`.  Record:
- File path
- The entry-point function's declared return annotation (verbatim)
- Whether the return value is wrapped (e.g. `Form4Bundle(...)`) or
  passed through (e.g. `return rows`)

- [ ] **Step 3: Identify the orphaned aggregator tests**

```bash
grep -rln "get_stock_signal_bundle\|StockSignalBundle" tests/
```

Record the file paths returned.  These are the test files to remove in
Task 18 (Phase D).

- [ ] **Step 4: Identify smart-money slicing sites**

```bash
grep -rn 'smart_money_data\["politicians"\]\|smart_money_data\["notable_holders"\]\|smart_money_data\[' src/agents/analysts/smart_money/
```

Record every file:line that reads the old category-first shape.  These
are the sites that change in Task 17.

- [ ] **Step 5: Write the audit document**

Use this skeleton.  One row per active domain.  The "Canonical shape"
column applies the principle from the spec (single / list / bundle).

```markdown
# Phase 7.6 — Provider shape audit

Recorded: <YYYY-MM-DD>.  Source for `DOMAIN_SHAPES` in Task 2.

## Provider domains

| Domain | Live entry-point | Cache entry-point | Live return type | Cache return type | Match? | Canonical shape | Drift fix needed |
|---|---|---|---|---|---|---|---|
| price_history | `src/data/providers/...` | `src/backtest/providers/...` | `...` | `...` | ✓ / ✗ | `list[OHLCBar]` | none / live / cache / both |
| company_ratios | ... | ... | ... | ... | ... | `CompanyRatios` | ... |
| ... (12 rows total) |
```

## Orphaned aggregator tests

- `tests/<path>.py` — exercises `get_stock_signal_bundle`
- `tests/<path>.py` — exercises `StockSignalBundle`
- `tests/<path>.py` — same

## Smart-money slicing sites

- `src/agents/analysts/smart_money/agent.py:<line>` — reads
  `state["smart_money_data"]["politicians"][ticker]`
- ... (one bullet per site)

- [ ] **Step 6: Commit**

```bash
git add docs/Phase7.5-more-cleanup/audit/provider_shapes.md
git commit -m "docs(phase7.6): audit live + cache provider return shapes

Records current return type per domain, match status against the
chosen canonical shape, the orphaned aggregator test paths, and the
smart-money slicing sites.  Drives DOMAIN_SHAPES (Task 2), the
contract-test xfail layout (Task 4), the per-domain Phase B fixes,
and the Phase D delete list."
```

---

### Task 2: Add `Provider[T]` Protocol + `DOMAIN_SHAPES` constant to the registry

**Files:**
- Modify: `src/data/registry.py`

- [ ] **Step 1: Read the current registry**

Confirm the existing module-level layout — imports, type aliases, and
the dispatch helper.  The additions go above the dispatch helper.

- [ ] **Step 2: Add the Protocol and `DomainShape` dataclass**

At the top of `src/data/registry.py`, after the existing imports, add:

```python
# --- Provider canonical-shape contracts (Phase 7.6) -------------------------
#
# Every registered provider for a given domain must return the same type as
# its peers.  DOMAIN_SHAPES is the single source of truth — the behavioural
# contract test in tests/contract/test_provider_shapes.py iterates this
# table and asserts each live + cache pair returns the canonical shape.

from dataclasses import dataclass
from typing import Literal, Protocol, TypeVar

T = TypeVar("T")


class Provider(Protocol[T]):
    """A registered data provider — async callable returning the
    canonical shape for its domain."""

    async def __call__(self, *args, **kwargs) -> T: ...


@dataclass(frozen=True)
class DomainShape:
    """Canonical return-shape for a registered provider domain.

    container:
      - "single"  one Pydantic model instance
      - "list"    list[payload_type]
      - "bundle"  a wrapper model with multiple sublists (e.g. Form4Bundle)

    payload_type: for "list", the element model class; for "single" and
    "bundle", the model class itself.
    """

    container: Literal["single", "list", "bundle"]
    payload_type: type
```

- [ ] **Step 3: Populate `DOMAIN_SHAPES` from the audit**

For each row in the audit's `## Provider domains` table, add an entry
to `DOMAIN_SHAPES`.  The exact 12 entries depend on audit findings;
the structure is fixed:

```python
# Populated from docs/Phase7.5-more-cleanup/audit/provider_shapes.md.
DOMAIN_SHAPES: dict[str, DomainShape] = {
    "price_history":     DomainShape("list",   OHLCBar),
    "company_ratios":    DomainShape("single", CompanyRatios),
    "news":              DomainShape("list",   NewsItem),
    "social_sentiment":  DomainShape("list",   SocialPost),
    "insider_trades":    DomainShape("bundle", Form4Bundle),
    "politician_trades": DomainShape("list",   PoliticianTrade),
    "notable_holders":   DomainShape("list",   NotableHolder),
    "filings":           DomainShape("list",   Filing),
    "earnings":          DomainShape("list",   EarningsEvent),
    "analyst_consensus": DomainShape("single", AnalystConsensus),
    "short_interest":    DomainShape("single", ShortInterest),
    "options":           DomainShape("list",   OptionContract),
}
```

Import each payload type from its canonical module at the top of
`registry.py`.  Where the audit shows a model name does not exist
(e.g. no `OptionContract` class today), it will be created or its
existing name confirmed inside the relevant Phase B task — for now,
fill the row with the most-natural name and add a `# TODO: confirm
type in <Task N>` comment **only if the audit reports the class is
absent**.  If the audit confirms the class exists, no comment.

- [ ] **Step 4: Run existing tests to confirm no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q`
Expected: PASS (additions are purely documentary; dispatch behaviour
unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/data/registry.py
git commit -m "feat(registry): add Provider[T] Protocol and DOMAIN_SHAPES

Introduces the canonical-shape contract for the 12 active provider
domains.  Storage in the registry stays Callable[..., Awaitable[Any]];
DOMAIN_SHAPES is consumed by the contract test in Task 4 as the single
source of truth for what each live + cache pair must return.

Populated from docs/Phase7.5-more-cleanup/audit/provider_shapes.md."
```

---

### Task 3: Add `SmartMoneyRaw` Pydantic model

**Files:**
- Create: `src/data/models/smart_money.py`
- Test: `tests/unit/data/models/test_smart_money.py`

- [ ] **Step 1: Write the failing test**

```python
"""Unit tests for SmartMoneyRaw — the per-ticker smart-money aggregate."""

import pytest
from pydantic import ValidationError

from data.models.smart_money import SmartMoneyRaw


def test_smart_money_raw_constructs_empty() -> None:
    """SmartMoneyRaw with no kwargs has empty lists, not None."""
    raw = SmartMoneyRaw()
    assert raw.politicians == []
    assert raw.notable_holders == []


def test_smart_money_raw_rejects_unknown_field() -> None:
    """extra='forbid' surfaces typos at construction time."""
    with pytest.raises(ValidationError):
        SmartMoneyRaw(politicans=[])  # typo (missing 'i')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/models/test_smart_money.py -v`
Expected: ImportError / ModuleNotFoundError on `data.models.smart_money`.

- [ ] **Step 3: Write the model**

```python
"""Per-ticker smart-money aggregate model.

Phase 7.6 introduces this model to replace the category-first nested
dict (`state["smart_money_data"]["politicians"][ticker]`) with a
ticker-first shape (`state["smart_money_data"][ticker]`).  See spec
docs/Phase7.5-more-cleanup/specs/data_shape_contracts.md §3
for rationale.
"""

from pydantic import BaseModel, ConfigDict, Field

# Imports adjusted per audit findings — Task 1 confirms the canonical
# locations of PoliticianTrade and NotableHolder.
from data.models.trades import PoliticianTrade
from data.models.trades import NotableHolder


class SmartMoneyRaw(BaseModel):
    """Per-ticker smart-money payload — politicians + notable holders for
    a single ticker.  Used as the value type in
    state["smart_money_data"][ticker] after Phase C of the data-shape
    contracts rollout.
    """

    model_config = ConfigDict(extra="forbid")

    politicians: list[PoliticianTrade] = Field(default_factory=list)
    notable_holders: list[NotableHolder] = Field(default_factory=list)
```

If the audit reveals `PoliticianTrade` and `NotableHolder` live in
different modules, adjust the imports accordingly.  The class names
themselves are stable.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/models/test_smart_money.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/data/models/smart_money.py tests/unit/data/models/test_smart_money.py
git commit -m "feat(models): add SmartMoneyRaw per-ticker aggregate model

Phase 7.6 introduces SmartMoneyRaw to replace the category-first nested
dict shape used in state['smart_money_data'] today.  The reshape itself
lands in Task 17; this commit only adds the model and confirms the
extra='forbid' guard rejects misspelled fields at construction time."
```

---

### Task 4: Register `contract` pytest marker and land the parametrised contract test

**Files:**
- Modify: `pytest.ini`
- Create: `tests/contract/__init__.py` (empty — package marker)
- Create: `tests/contract/test_provider_shapes.py`

- [ ] **Step 1: Register the new marker**

Edit `pytest.ini`.  After the `slow:` line, append:

```ini
    contract: behavioural contracts at layer boundaries (Phase 7.6+)
```

`--strict-markers` is already set in `addopts`, so unregistered markers
would fail the suite.  This must land before the contract test file.

- [ ] **Step 2: Create the test package init**

Create an empty `tests/contract/__init__.py`.

- [ ] **Step 3: Write the contract test**

```python
"""Behavioural contract: every registered provider for a domain returns
DOMAIN_SHAPES[domain].

Phase 7.6 lands this test with xfail markers on every domain whose live
and cache implementations are not yet aligned (per audit).  Each
Phase B task removes one xfail.
"""

from __future__ import annotations

import pytest

from data.registry import DOMAIN_SHAPES, DomainShape


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _matches_shape(value: object, shape: DomainShape) -> bool:
    """Return True iff value structurally matches the canonical shape."""

    if shape.container in ("single", "bundle"):
        return isinstance(value, shape.payload_type)

    if shape.container == "list":
        if not isinstance(value, list):
            return False
        return all(isinstance(item, shape.payload_type) for item in value)

    raise ValueError(f"unknown container: {shape.container!r}")


async def _call_live_provider(domain: str) -> object:
    """Construct the live provider for `domain`, mocking its HTTP/data
    boundary to return a minimal canned response, and call it once.

    Concrete mocking strategy per domain lives in fixtures below;
    this helper centralises the dispatch.
    """

    # Implementation populated alongside Phase B tasks — each domain's
    # mock returns a minimal canned response that the provider can
    # transform into the canonical shape.
    raise NotImplementedError(domain)


async def _call_cache_provider(domain: str) -> object:
    """Construct the cache provider for `domain` against an in-memory
    SQLite store seeded with one minimal row, and call it once.

    Domains with no cache provider raise pytest.skip in the test body
    (handled by the parametrisation below).
    """

    raise NotImplementedError(domain)


# ---------------------------------------------------------------------------
# Parametrisation
# ---------------------------------------------------------------------------

# Domains whose live and cache implementations disagree at the start of
# Phase 7.6.  Each Phase B task removes one entry here.  Source: audit
# document, "Match?" column = ✗.
_PENDING_ALIGNMENT: set[str] = {
    # Populated from audit in Task 1 — example entry:
    # "insider_trades",
}

# Domains with no cache provider today.  Test runs live-only for these.
# Source: audit document.
_LIVE_ONLY: set[str] = {
    "earnings",
    "analyst_consensus",
    "short_interest",
    "options",
}


def _params() -> list:
    """Build parametrisation entries with per-domain xfail marks."""

    entries = []
    for domain in sorted(DOMAIN_SHAPES.keys()):
        if domain in _PENDING_ALIGNMENT:
            entries.append(
                pytest.param(
                    domain,
                    marks=pytest.mark.xfail(
                        strict=True,
                        reason=f"{domain} live/cache shape drift — see Phase B task",
                    ),
                )
            )
        else:
            entries.append(pytest.param(domain))
    return entries


# ---------------------------------------------------------------------------
# Contract tests
# ---------------------------------------------------------------------------

@pytest.mark.contract
@pytest.mark.parametrize("domain", _params())
async def test_live_provider_returns_canonical_shape(domain: str) -> None:
    """Live provider for `domain` returns DOMAIN_SHAPES[domain]."""
    shape = DOMAIN_SHAPES[domain]
    result = await _call_live_provider(domain)
    assert _matches_shape(result, shape), (
        f"live provider for {domain!r} returned {type(result).__name__}, "
        f"expected container={shape.container} payload={shape.payload_type.__name__}"
    )


@pytest.mark.contract
@pytest.mark.parametrize("domain", _params())
async def test_cache_provider_returns_canonical_shape(domain: str) -> None:
    """Cache provider for `domain` returns DOMAIN_SHAPES[domain].

    Skipped for live-only domains (no cache provider exists today).
    """
    if domain in _LIVE_ONLY:
        pytest.skip(f"{domain} has no cache provider — live-only")

    shape = DOMAIN_SHAPES[domain]
    result = await _call_cache_provider(domain)
    assert _matches_shape(result, shape), (
        f"cache provider for {domain!r} returned {type(result).__name__}, "
        f"expected container={shape.container} payload={shape.payload_type.__name__}"
    )
```

- [ ] **Step 4: Implement `_call_live_provider` / `_call_cache_provider` per domain**

For each domain, fill in the dispatch branch in the two helpers.  The
exact mocking strategy per domain (yfinance monkey-patch vs httpx mock
vs in-memory `Store`) is dictated by the live provider's IO layer —
discovered during the audit.  Pattern for each branch:

```python
async def _call_live_provider(domain: str) -> object:
    if domain == "price_history":
        # Mock yfinance.download at module boundary
        ...
    elif domain == "company_ratios":
        # Mock the live HTTP call
        ...
    # ... 12 branches
    else:
        raise ValueError(f"no live-provider stub for domain: {domain!r}")
```

This is dense code but each branch is small (5-10 lines).  Lands
all together so the Phase B tasks each have a working test.

- [ ] **Step 5: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_provider_shapes.py -v`
Expected:
- Live-only domains (`earnings`, `analyst_consensus`, `short_interest`,
  `options`) → `skipped` for the cache test, `passed` for the live test.
- Pre-aligned domains → `passed` on both.
- Domains in `_PENDING_ALIGNMENT` → `xfailed` on the test that exercises
  the drifted implementation (live, cache, or both — phrasing in the
  audit row indicates which side has the bug).

If any row reports `xpassed` (strict-xfail failure), the test isn't
reaching the assertion or the drift was already fixed — debug before
committing.

- [ ] **Step 6: Commit**

```bash
git add pytest.ini tests/contract/__init__.py tests/contract/test_provider_shapes.py
git commit -m "test(contract): land provider-shape contract test

Parametrised over the 12 domains in DOMAIN_SHAPES.  Domains with
live/cache drift are xfail-staged per audit; each Phase B task
removes one xfail by aligning live and cache providers.  Live-only
domains skip the cache half.

Registers the 'contract' pytest marker."
```

---

## Phase B — Per-domain alignment (one task per domain)

Each task in this phase follows the same shape:

1. Confirm the audit row for the domain.
2. If aligned at start of phase: the contract test already passes for
   this domain; the task is a no-op except for documenting the row
   as "verified".
3. If drifted: apply the audit-prescribed edit to the live provider,
   cache provider, or both, until both return the canonical
   `DOMAIN_SHAPES[domain]`.
4. Remove the domain from `_PENDING_ALIGNMENT` in
   `tests/contract/test_provider_shapes.py`.
5. Run the full contract test and the full pytest suite.
6. Commit.

The tasks are listed in alphabetical order to remove ordering ambiguity.
The plan template below applies verbatim to each — only the audit-row
content differs.  Engineers executing this phase must consult
`docs/Phase7.5-more-cleanup/audit/provider_shapes.md` before
starting each task.

### Per-domain template (apply for every Task 5–16)

**Files (typical paths; confirm against audit row):**
- Modify (live): `src/data/providers/<domain>/...` — only if audit row
  marks live as the source of drift
- Modify (cache): `src/backtest/providers/<domain>_cache.py` — only if
  audit row marks cache as the source of drift
- Modify (model): `src/data/models/<domain>.py` — only if the audit row
  reveals a missing model class
- Modify: `tests/contract/test_provider_shapes.py` — remove the
  domain from `_PENDING_ALIGNMENT` (if it was in there)

- [ ] **Step 1: Re-read the audit row**

Open `docs/Phase7.5-more-cleanup/audit/provider_shapes.md` and
locate the row for this domain.  Confirm: live-current shape,
cache-current shape, canonical shape, drift-fix-needed column.

- [ ] **Step 2: Run the contract test for this domain only**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_provider_shapes.py -k "<domain>" -v
```

Expected behaviour:
- If audit said "Match? = ✓" → both live and cache tests `passed`.
- If audit said "Match? = ✗" → one or both tests `xfailed` (because the
  domain was registered in `_PENDING_ALIGNMENT` by Task 4).

If the actual result diverges from the audit prediction, stop and
re-audit before making code changes.

- [ ] **Step 3: Apply the drift fix (skip if Step 2 already green on both)**

Per the audit row's "Drift fix needed" column:
- `live` → edit the live provider to return the canonical shape.
- `cache` → edit the cache provider (usually a one-line change to
  match — e.g. drop or add a `Form4Bundle(...)` wrap).
- `both` → edit both.

Show the smallest possible diff that aligns the implementation with
the canonical shape.  Do not refactor surrounding code unless required
for type correctness.

- [ ] **Step 4: Re-run the domain contract test**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_provider_shapes.py -k "<domain>" -v
```

Expected: live + cache both `passed` (or just live for live-only
domains; cache is skipped).

- [ ] **Step 5: Remove the domain from `_PENDING_ALIGNMENT`**

Edit `tests/contract/test_provider_shapes.py`.  Delete the domain's
string from the `_PENDING_ALIGNMENT` set.  Run:

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_provider_shapes.py -k "<domain>" -v
```

Expected: live + cache `passed` (not `xfailed`).  If `xpassed`
appears, the strict-xfail marker is still in effect and the dict
edit missed.

- [ ] **Step 6: Run the full pytest suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -m "not slow and not integration" -q
```

Expected: PASS.  Any failures must be addressed before commit (no
broken-suite commits between Phase B tasks).

- [ ] **Step 7: Commit**

```bash
git add <changed files>
git commit -m "refactor(<domain>): align live and cache return shape

Both implementations now return DOMAIN_SHAPES['<domain>'] —
<container> / <payload_type>.

Contract test row removed from _PENDING_ALIGNMENT."
```

### Per-domain task list (Tasks 5–16)

Each domain gets its own task slot.  The engineer executes them in the
order listed:

- **Task 5: `analyst_consensus`** — apply per-domain template.
- **Task 6: `company_ratios`** — apply per-domain template.
- **Task 7: `earnings`** — apply per-domain template (live-only).
- **Task 8: `filings`** — apply per-domain template.
- **Task 9: `insider_trades`** — apply per-domain template.  Known
  drift case: live returns
  `tuple[list[InsiderTrade], list[InsiderDerivativeTransaction]]`
  while cache wraps as `Form4Bundle(...)`.  Canonical shape:
  `bundle / Form4Bundle`.  Fix is to update the live entry-point to
  wrap its return value in a `Form4Bundle` (single-line change near
  the `return` statement; pre-existing tuple consumers must
  simultaneously be updated to attribute access — discovered during
  Step 3).
- **Task 10: `news`** — apply per-domain template.
- **Task 11: `notable_holders`** — apply per-domain template.
- **Task 12: `options`** — apply per-domain template (live-only).
- **Task 13: `politician_trades`** — apply per-domain template.
- **Task 14: `price_history`** — apply per-domain template.
- **Task 15: `short_interest`** — apply per-domain template (live-only).
- **Task 16: `social_sentiment`** — apply per-domain template.

After Task 16, `_PENDING_ALIGNMENT` must be the empty set `set()`.

---

## Phase C — Smart-money reshape

### Task 17: Reshape `state["smart_money_data"]` to ticker-first per-ticker `SmartMoneyRaw`

This task does three coupled edits in one commit because they change
the state shape together — any single edit in isolation would break the
suite mid-commit.

**Files:**
- Modify: `src/agents/analysts/smart_money/fetch.py:81-110`
- Modify: `src/agents/analysts/smart_money/agent.py` (every site identified
  by the audit's "Smart-money slicing sites" list)
- Modify: relevant smart-money unit tests (audit-identified)

- [ ] **Step 1: Re-read the audit's smart-money slicing-sites list**

Open the audit document.  Note every `agent.py:<line>` entry and any
test files listed.

- [ ] **Step 2: Write a failing unit test for the new shape**

In the smart-money unit test file (path per audit; typically
`tests/unit/agents/analysts/smart_money/test_fetch.py`), add:

```python
async def test_fetch_callback_writes_ticker_first_smart_money_raw(
    monkeypatch,
) -> None:
    """fetch callback writes state['smart_money_data'][ticker] as SmartMoneyRaw."""

    from agents.analysts.smart_money import fetch as smart_money_fetch
    from data.models.smart_money import SmartMoneyRaw

    # Stub provider calls to return minimal known data for AAPL.
    # ... fixture setup per existing test patterns ...

    class FakeCtx:
        state = {"tickers": ["AAPL"], "as_of": <as_of>}

    await smart_money_fetch.smart_money_fetch_callback(FakeCtx())

    payload = FakeCtx.state["smart_money_data"]["AAPL"]
    assert isinstance(payload, SmartMoneyRaw)
    assert payload.politicians == []         # or whatever the stub returns
    assert payload.notable_holders == []     # ditto
```

- [ ] **Step 3: Run the new test (expect failure)**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/smart_money/ -k test_fetch_callback_writes_ticker_first_smart_money_raw -v`
Expected: FAIL — old code writes category-first shape.

- [ ] **Step 4: Rewrite `fetch.py` lines 81–110 to per-ticker shape**

Replace the existing block:

```python
smart_money_data: dict = {
    "politicians": {},
    "notable_holders": {},
}
for ticker in tickers:
    smart_money_data["politicians"][ticker] = [t.model_dump() ...]
    smart_money_data["notable_holders"][ticker] = [h.model_dump() ...]
state["smart_money_data"] = smart_money_data
```

With:

```python
from data.models.smart_money import SmartMoneyRaw

smart_money_data: dict[str, SmartMoneyRaw] = {}
for ticker in tickers:
    politicians_raw = ...     # the existing fetch result for this ticker
    notable_holders_raw = ...

    smart_money_data[ticker] = SmartMoneyRaw(
        politicians=politicians_raw,
        notable_holders=notable_holders_raw,
    )

state["smart_money_data"] = smart_money_data
```

Note: the value is the `SmartMoneyRaw` model instance, not a `.model_dump()`.
Downstream consumers slice attributes, not dict keys.

- [ ] **Step 5: Rewrite each agent.py slicing site**

For every site the audit identified:

```python
# Before
politicians = state["smart_money_data"]["politicians"].get(ticker, [])
holders = state["smart_money_data"]["notable_holders"].get(ticker, [])

# After
raw = state["smart_money_data"].get(ticker)
if raw is None:
    politicians, holders = [], []
else:
    politicians = raw.politicians
    holders = raw.notable_holders
```

Apply the same translation to any prompt-formatting code that pokes
into the nested dict.

- [ ] **Step 6: Update existing smart-money unit tests**

Any test fixture that constructs the old category-first dict must
rebuild as ticker-first.  Update test inputs in-place — keep the same
expected outcomes.

- [ ] **Step 7: Run the smart-money tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -k "smart_money" -v`
Expected: all tests pass, including the new one from Step 2.

- [ ] **Step 8: Run the full pytest + smoke**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -m "not slow and not integration" -q`
Expected: PASS.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`
Expected: PASS (the smoke test exercises the smart-money flow under
mocked LLMs; the reshape must not break the orchestration).

- [ ] **Step 9: Commit**

```bash
git add src/agents/analysts/smart_money/fetch.py \
        src/agents/analysts/smart_money/agent.py \
        tests/unit/agents/analysts/smart_money/
git commit -m "refactor(smart_money): reshape state to ticker-first SmartMoneyRaw

state['smart_money_data'] is now keyed ticker-first with SmartMoneyRaw
values instead of category-first nested dicts.  Fetch callback
constructs SmartMoneyRaw at the write site (extra='forbid' guards
against future typo drift).  Every slicing/prompt-formatting site in
the smart-money agent updated to attribute access.

End-to-end smoke test passes."
```

---

## Phase D — Aggregator delete

### Task 18: Delete `src/data/aggregator.py`, orphaned tests, and `__init__.py` re-exports

**Files:**
- Delete: `src/data/aggregator.py`
- Delete: each test file identified in the audit's "Orphaned aggregator
  tests" section
- Modify: `src/data/__init__.py` — remove `get_stock_signal_bundle`,
  `get_stock_signal_bundle_blocking`, `StockSignalBundle` from
  `__all__` and from any re-export lines

If Phase 7.5 amendments did not happen before this task runs (and 7.5
landed the `test_aggregator_uses_config_lookbacks` xfail-then-pass
flow), additional deletions:
- Modify: `tests/contract/test_lookbacks_sourced_from_config.py` —
  remove the `test_aggregator_uses_config_lookbacks` function entirely

- [ ] **Step 1: Re-read the audit's "Orphaned aggregator tests" list**

Confirm the exact file paths.  Some of these may be whole files; some
may be individual test functions inside a shared file.  The audit's
note column distinguishes.

- [ ] **Step 2: Confirm zero production callers (sanity grep)**

```bash
grep -rn "get_stock_signal_bundle\|StockSignalBundle" src/ scripts/
```

Expected: hits only inside `src/data/aggregator.py` and
`src/data/__init__.py`.  If any other file appears, stop and audit
the caller — production-code callers were ruled out at spec time, so
a hit here represents a regression since the spec landed.

- [ ] **Step 3: Delete the files / functions**

```bash
git rm src/data/aggregator.py
git rm <each orphaned test file from audit>
```

For test files where only a function (not the whole file) is to be
deleted, edit the file in place.

- [ ] **Step 4: Edit `src/data/__init__.py`**

Remove every line referencing `get_stock_signal_bundle`,
`get_stock_signal_bundle_blocking`, or `StockSignalBundle` — both the
`from .aggregator import …` lines and the corresponding entries in
`__all__`.

- [ ] **Step 5: Re-run the grep (must return zero hits)**

```bash
grep -rn "get_stock_signal_bundle\|StockSignalBundle" src/ tests/
```

Expected: no output.  If any hit remains, it is a stale reference
the previous steps missed — fix it before committing.

- [ ] **Step 6: Run the full pytest suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -m "not slow and not integration" -q`
Expected: PASS.  Collection errors here mean an import survived the
edit — re-grep and fix.

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove dead aggregator surface

Deletes src/data/aggregator.py, three orphaned aggregator tests, and
the get_stock_signal_bundle / StockSignalBundle re-exports from
src/data/__init__.py.  Zero production callers — confirmed via grep
across src/ and scripts/.

Closes docs/todo-fixes.md item 2.3."
```

---

## Phase E — Final sweep

### Task 19: Verify acceptance criteria; write `done.md`

**Files:**
- Create: `docs/Phase7.5-more-cleanup/done.md`

- [ ] **Step 1: Run every acceptance grep**

```bash
# (a) No remaining xfail markers in the provider-shape contract test
grep -n "xfail" tests/contract/test_provider_shapes.py
```
Expected: no output.

```bash
# (b) No aggregator references anywhere in code or tests
grep -rn "get_stock_signal_bundle\|StockSignalBundle" src/ tests/ scripts/
```
Expected: no output.

```bash
# (c) _PENDING_ALIGNMENT is the empty set in the contract test
grep -n "_PENDING_ALIGNMENT" tests/contract/test_provider_shapes.py
```
Expected: a single line declaring `_PENDING_ALIGNMENT: set[str] = set()`.

```bash
# (d) DOMAIN_SHAPES has exactly 12 entries
grep -c "DomainShape(" src/data/registry.py
```
Expected: `12`.

```bash
# (e) No category-first slicing patterns survive in smart-money
grep -rn 'smart_money_data\["politicians"\]\|smart_money_data\["notable_holders"\]' src/ tests/
```
Expected: no output.

- [ ] **Step 2: Run the full pytest suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest -m "not slow and not integration" -q
```
Expected: full pass; the new `contract` marker is exercised.

- [ ] **Step 3: Run the end-to-end smoke**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```
Expected: pass.

- [ ] **Step 4: Write `done.md`**

```markdown
# Phase 7.6 — Data-shape contracts — done

**Closed:** <YYYY-MM-DD>
**Commits:** ~19 (one audit, four foundations, twelve per-domain,
one smart-money reshape, one aggregator delete, one final sweep)

## What landed

- `Provider[T]` Protocol + `DomainShape` dataclass + 12-entry
  `DOMAIN_SHAPES` in `src/data/registry.py`
- Parametrised contract test
  `tests/contract/test_provider_shapes.py` enforcing live + cache
  return shape per domain
- `SmartMoneyRaw` Pydantic model — replaces the category-first
  nested-dict shape in `state["smart_money_data"]`
- 12 per-domain alignment commits (see git log for details; audit
  document records the original drift cases)
- Deletion of `src/data/aggregator.py`, three orphaned tests, and
  the `__all__` re-exports

## Acceptance grep evidence

(paste from Step 1 output)

## Risks recorded for the next phase

- Any future provider added to the registry must also add an entry
  to `DOMAIN_SHAPES` and pass the contract test — there is no
  startup-time check that catches an omission.  Mitigation deferred
  per spec's "anything you want strengthened here?" gate.

## Follow-ups

- Phase 7.5 amendments (separate task, queued post-7.6) to defer
  aggregator config-routing work that 7.6 deleted.
```

- [ ] **Step 5: Commit**

```bash
git add docs/Phase7.5-more-cleanup/done.md
git commit -m "docs(phase7.6): close out — done.md

Records acceptance-criteria grep output, final commit count, and the
follow-up note for Phase 7.5 spec/plan amendments."
```

---

## Risks

### R1 — Audit prediction wrong on one or more domains

If the audit row says "Match? ✓" but the contract test reports
`xfailed` at first run, an underlying assumption shifted between
audit (Task 1) and contract-test execution (Task 4).

**Mitigation.**  Each Phase B task re-runs Step 2 (read audit row)
before any code change.  Mismatch between audit prediction and test
behaviour means stop and re-audit — never paper over with a code
edit that wasn't audit-prescribed.

### R2 — A model class named in `DOMAIN_SHAPES` does not exist

E.g. `OptionContract` may not exist as a Pydantic model today.

**Mitigation.**  Task 2 marks unknown classes with a `# TODO: confirm
type in <Task N>` comment.  The relevant Phase B task either confirms
the class exists (and removes the comment) or creates it (small,
focused diff) before aligning the provider.

### R3 — Smart-money reshape (Task 17) breaks the end-to-end smoke

Reshape touches state-shape consumed at agent-pipeline run time;
mocked-LLM smoke test is the only behavioural canary.

**Mitigation.**  Step 8 of Task 17 runs the smoke test explicitly.
If it fails, root cause is one of: (a) a slicing site the audit
missed; (b) a prompt-formatting site the audit missed; (c) a
downstream agent that reads `smart_money_data` directly.  Re-grep
for the old pattern and audit hits before commit.

### R4 — Phase 7.5 has not yet landed the aggregator amendments

If Phase 7.6 runs before Phase 7.5 amendments, then Task 18 deletes
content that did not exist at audit time (e.g.
`test_aggregator_uses_config_lookbacks` added by 7.5).

**Mitigation.**  Task 18 Step 1 instructs the engineer to re-read the
audit's "Orphaned aggregator tests" section *just before deleting*.
If 7.5 landed the new test between audit and Task 18 execution, the
engineer adds the new test to the deletion list.  This is fine — the
delete step is grep-driven, not audit-frozen.

---

## Self-review

Self-checked against the spec on 2026-05-18:

- **Spec coverage:** Every spec section (audit, Protocol/`DOMAIN_SHAPES`,
  `SmartMoneyRaw`, contract test with xfail, per-domain alignment,
  smart-money reshape, aggregator delete, final sweep) maps to at
  least one task.  ✓
- **Placeholder scan:** Three places (audit row contents, contract-test
  helper bodies, `done.md` template) are honestly "filled by the
  preceding step in this plan" — not laziness; they require data
  that only exists once execution begins.  No bare TBD / TODO /
  "implement later".  ✓
- **Type consistency:** `DomainShape`, `Provider[T]`, `SmartMoneyRaw`,
  `DOMAIN_SHAPES` referenced with identical names across Tasks 2, 3,
  4, 17, 19.  ✓

No issues found that warrant a rewrite.

---

## Execution handoff

Plan complete and saved to
`docs/Phase7.5-more-cleanup/plans/data_shape_contracts_v1.md`.

Two execution options:

1. **Subagent-driven (recommended)** — dispatch a fresh subagent per
   task, review between tasks, fast iteration.
2. **Inline execution** — execute in the current session via
   `superpowers:executing-plans`, batch with checkpoints.

The amendments to Phase 7.5 (deferring aggregator config-routing
work) are queued as a separate task (#21) to run before this plan
executes, so Phase 7.5 doesn't introduce churn that 7.6 immediately
deletes.

Which approach for execution?
