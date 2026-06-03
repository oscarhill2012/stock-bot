# Plan 08 — Data-provider cull + registry validation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cull dead provider modules, dormant data schemas, and unused
executor / memory / llm-retry helpers; tighten `set_active_provider` to refuse
unknown provider names; keep the project's "swap is a one-line config edit"
contract intact by leaving every surviving provider registered as a fallback
shell.

**Architecture:** Surgical deletions ordered smallest-blast-first (schemas →
provider modules → dead helpers), then a single behaviour change to
`set_active_provider` plus the matching `config/data.json` and
`config/README.md` updates. Plan 01 must have landed (safe deletions) so the
auditing baseline is clean; Plans 02–07 are disjoint. Plan 09 (reference-symbol
consolidation) trusts only the surviving providers/domains listed in this
plan's inventory table to exist after we land.

**Tech Stack:** Python 3.x, Pydantic v2, pytest, project `data.registry`
+ `data.config` loaders, `config/data.json`.

**Findings covered (cross-checked against `FINDINGS.md`):**

- **A-029** — memory DI setters dead (`src/agents/memory/embeddings.py:6-13`,
  `src/agents/memory/compress.py:9-15`)
- **A-032** — `_is_schema_error` ImportError silent downgrade
  (`src/agents/llm_retry.py:170-175,206-211,246-272` — pydantic is a hard dep)
- **A-036** — four unused Phase-3 providers + tests (~975 LoC):
  `earnings/finnhub`, `analyst_consensus/yfinance`, `short_interest/finra`,
  `options/yfinance`
- **A-037** — `news/alpha_vantage` dead (757 LoC + test file)
- **A-038** — `company_ratios.yfinance` duplicate registration inside
  `src/data/providers/stats/yfinance.py:527-572` (no swap call site)
- **A-064** — `resolve_broker_call` zero callers
  (`src/agents/executor/_verb_dispatch.py:84-141` + four tests in
  `tests/unit/agents/executor/test_verb_dispatch.py`)
- **A-082** — 7 dormant data schemas: `EarningsHistory`, `EarningsReport`,
  `AnalystConsensusBundle`, `AnalystRating`, `AnalystRevision`,
  `ShortInterestSnapshot`, `OptionContract`
- **A-041** (companion to A-036) — `set_active_provider` accepts unregistered
  names (`src/data/registry.py:196-235`). Membership-check refactor in scope
  because it's the natural guard that prevents the cull from silently being
  undone by a typo in `config/data.json`.

**Trust contract**

- **Trusts Plan 01 landed** — safe deletions (the no-blast-radius dead-file
  removals) already applied. This plan assumes a clean tree.
- **Disjoint from Plans 02–07.** No shared files, no shared schemas.
- **Plan 09 (reference-symbol consolidation) trusts this plan's surviving
  domain set.** After we land, the provider/domain map below is the
  authoritative inventory — Plan 09 will reference only those.
- **Cross-cutting contract:** every registered data provider keeps the same
  call signature; provider swaps are `config/data.json` edits, **never** code
  changes. The cull removes truly unused providers (no analyst consumer) but
  preserves the shell pattern for every domain we still ship.

---

## Provider / schema inventory

The table below is the **authoritative inventory** for downstream plans. Each
row is the post-cull state. Any consumer of a "DELETE" row anywhere in `src/`,
`tests/`, `scripts/`, or `config/` is a regression — see the silent-regression
checklist.

### Domains (post-cull)

| Domain | Active provider | Fallback shell(s) | Status |
|---|---|---|---|
| `price_history` | `yfinance` (`stats/yfinance.py`) | none | KEEP |
| `company_ratios` | `pit_composite` | none (was: `yfinance` via `stats/yfinance.py:527-572`) | DELETE duplicate registration (A-038) |
| `news` | `finnhub` | `tiingo` | DELETE `alpha_vantage` (A-037) |
| `social_sentiment` | `finnhub` | none | KEEP |
| `insider_trades` | `edgar` | none | KEEP |
| `politician_trades` | `fmp` | `quiver` (commented-out in fetcher per memory) | KEEP both registrations |
| `notable_holders` | `edgar` | none | KEEP |
| `filings` | `edgar` | none | KEEP |
| `earnings` | — | — | DELETE domain (A-036, A-082) |
| `analyst_consensus` | — | — | DELETE domain (A-036, A-082) |
| `short_interest` | — | — | DELETE domain (A-036, A-082) |
| `options` | — | — | DELETE domain (A-036, A-082) |

**Resulting `DOMAINS` set (8):** `price_history`, `company_ratios`, `news`,
`social_sentiment`, `insider_trades`, `politician_trades`, `notable_holders`,
`filings`. This matches §7.4's authoritative count per `FINDINGS.md` A-036.

### Data schemas (post-cull)

| File | Class(es) | Status |
|---|---|---|
| `src/data/models/earnings.py` | `EarningsReport`, `EarningsHistory` | DELETE (A-082) |
| `src/data/models/analyst_consensus.py` | `AnalystRating`, `AnalystRevision`, `AnalystConsensusBundle` | DELETE (A-082) |
| `src/data/models/short_interest.py` | `ShortInterestSnapshot` | DELETE (A-082) |
| `src/data/models/options.py` | `OptionContract` | DELETE (A-082) |
| all other `src/data/models/*.py` | — | KEEP |

### Helpers / DI seams (post-cull)

| File | Status |
|---|---|
| `src/agents/memory/embeddings.py::set_embedding_provider`, `_embedding_provider`, `_default_embed` indirection | DELETE setter + module global; collapse `embed()` to call `_default_embed` directly (A-029) |
| `src/agents/memory/compress.py::set_compress_llm`, `_compress_llm` module global | DELETE setter + global; `compress()` keeps its existing `llm_fn` parameter (A-029) |
| `src/agents/llm_retry.py` `try/except ImportError` around `from pydantic import ValidationError` (two sites: `_is_schema_error`, `_find_validation_error`) | DELETE guards; import unconditionally (A-032) |
| `src/agents/executor/_verb_dispatch.py::resolve_broker_call` (+ `HALLUCINATED` sentinel if unused elsewhere) | DELETE function (A-064) |

### Behaviour change

| Site | Change |
|---|---|
| `src/data/registry.py::set_active_provider` (lines 196-235) | Raise `ValueError` when `(domain, name)` is not in `_REGISTRY` (currently only `domain` membership is checked). |

---

## Ordered changes

Order is chosen to minimise blast radius and keep each commit independently
revertable. Schemas first (smallest), then provider modules, then dead
helpers, then the registry behaviour change, then config + README updates.

Absolute file paths throughout. Run all `pytest` / `ruff` commands from the
project root with the venv interpreter directly (no `cd` prefix — see project
`CLAUDE.md`).

### Task 1: Establish baseline (no code changes)

**Files:** none

- [ ] **Step 1: Confirm current test suite passes**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (or document the existing failure set so we don't blame the cull).

- [ ] **Step 2: Confirm ruff is clean**

Run: `.venv/bin/python -m ruff check src/ tests/ scripts/`
Expected: PASS.

- [ ] **Step 3: Capture the active provider set**

Run: `.venv/bin/python -c "from src.data.config import get_config; import json; print(json.dumps(get_config().providers, indent=2))"`
Expected: prints the 12 current domain→provider entries from `config/data.json`.
Save this output in the PR description as the "before" baseline.

---

### Task 2: Delete `OptionContract` schema + `options` domain

**Files:**
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/models/options.py`
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/options/` (whole directory)
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_options_yfinance_shell.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/config/data.json`

- [ ] **Step 1: Find every reference to `OptionContract` or `options` domain**

Run: `grep -rn "OptionContract\|providers\.options\|\"options\"" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts /home/oscarhill2012/Documents/Repository/StockBot/config 2>/dev/null | grep -v __pycache__`
Expected: matches only in the four files listed above plus `config/README.md` (which Task 9 updates).

If any analyst, extractor, or backtest provider references either symbol, **STOP** — the cull is unsafe; revisit the inventory.

- [ ] **Step 2: Delete the model and provider files**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/data/models/options.py
rm -r /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/options/
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_options_yfinance_shell.py
```

- [ ] **Step 3: Remove `options` from `src/data/registry.py`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`:

Remove the import line:
```python
from .models.options import OptionContract
```
Remove the `DOMAIN_SHAPES` entry:
```python
"options":           DomainShape("list",   OptionContract),
```
Remove the `DOMAINS` entry (and its `# options …` trailing comment):
```python
"options",             # yfinance options chain (live-only shell)
```

- [ ] **Step 4: Remove the options registration import**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py` to drop:
```python
from .options import yfinance as _options_yfinance  # noqa: F401  — Task 3.7
```

- [ ] **Step 5: Remove `"options": "yfinance"` from `config/data.json`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/config/data.json` — drop the `"options"` line from the `"providers"` map. Mind the trailing comma on the preceding line.

- [ ] **Step 6: Run the targeted test slice**

Run: `.venv/bin/python -m pytest tests/unit/data/ tests/contract/ -q`
Expected: PASS — no test should still reference `options`.

- [ ] **Step 7: Run full suite + ruff**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -- src/data/models/options.py src/data/providers/options src/data/registry.py src/data/providers/__init__.py config/data.json tests/unit/data/providers/test_options_yfinance_shell.py
git commit -m "chore(data): delete options domain — zero consumers (A-036, A-082)"
```

---

### Task 3: Delete `short_interest` domain + `ShortInterestSnapshot`

**Files:**
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/models/short_interest.py`
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/short_interest/` (whole directory)
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_short_interest_finra_as_of.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/config/data.json`

- [ ] **Step 1: Reference-scan**

Run: `grep -rn "ShortInterestSnapshot\|short_interest" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts 2>/dev/null | grep -v __pycache__`
Expected: only the files listed above. STOP if anything else surfaces.

- [ ] **Step 2: Delete model, provider, test**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/data/models/short_interest.py
rm -r /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/short_interest/
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_short_interest_finra_as_of.py
```

- [ ] **Step 3: Strip from `registry.py`**

Remove from `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`:
- `from .models.short_interest import ShortInterestSnapshot`
- the `"short_interest": DomainShape("list", ShortInterestSnapshot),` entry in `DOMAIN_SHAPES`
- the `"short_interest", # FINRA short-interest …` entry in `DOMAINS`

- [ ] **Step 4: Strip from `providers/__init__.py`**

Remove the line:
```python
from .short_interest import finra as _short_interest_finra  # noqa: F401  — Task 3.3
```

- [ ] **Step 5: Strip `"short_interest": "finra"` from `config/data.json`**

- [ ] **Step 6: Test + ruff + commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/
git add -- src/data/models/short_interest.py src/data/providers/short_interest src/data/registry.py src/data/providers/__init__.py config/data.json tests/unit/data/providers/test_short_interest_finra_as_of.py
git commit -m "chore(data): delete short_interest domain — zero consumers (A-036, A-082)"
```

---

### Task 4: Delete `analyst_consensus` domain + its three schema classes

**Files:**
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/models/analyst_consensus.py`
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/analyst_consensus/` (whole directory)
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_analyst_consensus_yfinance.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/config/data.json`

- [ ] **Step 1: Reference-scan**

Run: `grep -rn "AnalystConsensusBundle\|AnalystRating\|AnalystRevision\|analyst_consensus" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts 2>/dev/null | grep -v __pycache__`

Note: there is an `agents/analysts/` package — that's the LLM analyst agents, not this data domain. The grep above should only hit the listed files plus `data/models/__init__.py` if it re-exports. STOP and reconcile if any agent file shows a true import.

- [ ] **Step 2: Delete the files**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/data/models/analyst_consensus.py
rm -r /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/analyst_consensus/
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_analyst_consensus_yfinance.py
```

- [ ] **Step 3: Strip from `registry.py`**

Remove the import, the `DOMAIN_SHAPES["analyst_consensus"]` entry, and the `DOMAINS` entry (with its `# yfinance analyst ratings aggregation` comment).

- [ ] **Step 4: Strip the import from `providers/__init__.py`**

Remove:
```python
from .analyst_consensus import yfinance as _analyst_consensus_yfinance  # noqa: F401  — Task 3.6
```

- [ ] **Step 5: Strip `"analyst_consensus": "yfinance"` from `config/data.json`**

- [ ] **Step 6: Test + ruff + commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/
git add -- src/data/models/analyst_consensus.py src/data/providers/analyst_consensus src/data/registry.py src/data/providers/__init__.py config/data.json tests/unit/data/providers/test_analyst_consensus_yfinance.py
git commit -m "chore(data): delete analyst_consensus domain — zero consumers (A-036, A-082)"
```

---

### Task 5: Delete `earnings` domain + `EarningsHistory`/`EarningsReport`

**Files:**
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/models/earnings.py`
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/earnings/` (whole directory)
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_earnings_finnhub_as_of.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/config/data.json`

- [ ] **Step 1: Reference-scan**

Run: `grep -rn "EarningsHistory\|EarningsReport\|\"earnings\"\|providers\.earnings" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts 2>/dev/null | grep -v __pycache__`
Expected: matches only in the files listed above. STOP otherwise.

- [ ] **Step 2: Delete files**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/data/models/earnings.py
rm -r /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/earnings/
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_earnings_finnhub_as_of.py
```

- [ ] **Step 3: Strip from `registry.py`**

Remove the import, `DOMAIN_SHAPES["earnings"]`, and the `DOMAINS` entry (with the `# Finnhub earnings calendar / actuals` comment).

- [ ] **Step 4: Strip from `providers/__init__.py`**

Remove:
```python
from .earnings import finnhub as _earnings_finnhub  # noqa: F401  — Task 3.1
```

- [ ] **Step 5: Strip `"earnings": "finnhub"` from `config/data.json`**

- [ ] **Step 6: Test + ruff + commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/
git add -- src/data/models/earnings.py src/data/providers/earnings src/data/registry.py src/data/providers/__init__.py config/data.json tests/unit/data/providers/test_earnings_finnhub_as_of.py
git commit -m "chore(data): delete earnings domain — zero consumers (A-036, A-082)"
```

---

### Task 6: Delete `news/alpha_vantage` provider (keep `finnhub` + `tiingo`)

**Files:**
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/news/alpha_vantage.py`
- Delete: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_news_alpha_vantage_as_of.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/__init__.py`

This is a provider-level cull only — `news` stays a domain, `finnhub` remains
active, `tiingo` remains the registered fallback shell.

- [ ] **Step 1: Reference-scan for alpha_vantage news**

Run: `grep -rn "news.*alpha_vantage\|alpha_vantage.*news\|news_alpha_vantage" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts /home/oscarhill2012/Documents/Repository/StockBot/config 2>/dev/null | grep -v __pycache__`
Expected: only the three files listed above plus `config/README.md` Phase-6
narrative (Task 9 updates that).

- [ ] **Step 2: Delete the provider module and its test**

```bash
rm /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/news/alpha_vantage.py
rm /home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/providers/test_news_alpha_vantage_as_of.py
```

- [ ] **Step 3: Drop the import from `providers/__init__.py`**

Remove:
```python
from .news import alpha_vantage as _news_alpha_vantage  # noqa: F401  — Task 3.2
```

- [ ] **Step 4: Sanity-check `NewsArticle.sentiment` / `.relevance` fields**

Per A-037 the model carries `sentiment` and `relevance` fields that alpha_vantage was the only writer for; they are intentionally `None` in production (memory: `news_sentiment_intentionally_null`). Keep the fields — Finnhub leaves them `None` and the downstream extractor defaults to `0.0`. **Do not** strip the fields in this plan.

- [ ] **Step 5: Test + ruff + commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/
git add -- src/data/providers/news/alpha_vantage.py src/data/providers/__init__.py tests/unit/data/providers/test_news_alpha_vantage_as_of.py
git commit -m "chore(data): delete news/alpha_vantage — finnhub is active, tiingo is fallback (A-037)"
```

---

### Task 7: Delete `company_ratios.yfinance` duplicate registration

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/stats/yfinance.py` (delete lines 527-572 — the `@register(domain="company_ratios", name="yfinance", …)` block)

The `stats/yfinance.py` module also hosts the active `price_history` provider
— we keep that. Only the secondary `company_ratios` registration goes.
`pit_composite` remains the sole `company_ratios` provider; there is no other
fallback to keep.

- [ ] **Step 1: Confirm zero callers of the duplicate registration**

Run: `grep -rn "company_ratios.*yfinance\|\"yfinance\".*company_ratios" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts /home/oscarhill2012/Documents/Repository/StockBot/config 2>/dev/null | grep -v __pycache__`
Expected: hits in `stats/yfinance.py` itself, `config/README.md` Phase-6 note, and possibly test commentary. **No** active config entry (since `pit_composite` is selected). STOP if a swap call site appears.

- [ ] **Step 2: Open `stats/yfinance.py` and delete the duplicate block**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/stats/yfinance.py` and remove the `@register(domain="company_ratios", name="yfinance", …)` decorator block plus its `fetch_company_ratios` async function (currently lines 527-572). Also drop any helper symbols (e.g. `_fetch_company_ratios`) that become unreferenced once the public wrapper is gone — verify with a follow-up grep before deleting.

- [ ] **Step 3: Drop the now-unused `CompanyRatios` import if no other symbol in the file uses it**

Run: `grep -n "CompanyRatios" /home/oscarhill2012/Documents/Repository/StockBot/src/data/providers/stats/yfinance.py`
Expected after edit: no remaining references → remove the import. If references remain (e.g. type hints in a helper still used by `price_history`), keep the import.

- [ ] **Step 4: Test + ruff + commit**

```bash
.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/ tests/ scripts/
git add -- src/data/providers/stats/yfinance.py
git commit -m "chore(data): drop company_ratios.yfinance duplicate — pit_composite is sole provider (A-038)"
```

---

### Task 8: Tighten `set_active_provider` — raise on unknown provider

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py`
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/test_set_active_provider_validation.py`

This is the registry behaviour change that prevents the cull from being
silently undone by a typo in `config/data.json` or by a future swap call to a
non-existent provider name. It enforces the contract that **swap targets must
be a registered `(domain, name)` pair**.

- [ ] **Step 1: Write the failing test**

Create `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/test_set_active_provider_validation.py`:

```python
"""set_active_provider must refuse unknown provider names.

Audit A-041: the runtime swap previously accepted any string, only failing at
the next dispatch with a confusing KeyError. The contract is: a swap target
must be a registered ``(domain, name)`` pair. Anything else is a typo and we
fail loudly, immediately.
"""
from __future__ import annotations

import pytest

# Import the providers package so the @register decorators populate the
# registry before we exercise set_active_provider.
import data.providers  # noqa: F401 — import-for-side-effects
from data.registry import set_active_provider


def test_set_active_provider_raises_on_unknown_provider_name():
    """Unknown provider name on a known domain → ValueError, no swap applied."""

    with pytest.raises(ValueError, match="no provider registered"):
        set_active_provider("news", "nonexistent_provider_xyz")


def test_set_active_provider_raises_on_unknown_domain():
    """Unknown domain still raises (pre-existing behaviour, kept)."""

    with pytest.raises(ValueError, match="unknown domain"):
        set_active_provider("not_a_real_domain", "anything")


def test_set_active_provider_accepts_registered_pair_and_restores():
    """The happy path still works — swap to a registered provider, restore."""

    # Pick a domain that has a single registered provider (post-cull) and
    # swap it to itself; the restore callable must put the original back.
    from data.config import get_config

    cfg = get_config()
    original = cfg.providers["price_history"]

    restore = set_active_provider("price_history", original)

    assert cfg.providers["price_history"] == original

    restore()

    assert cfg.providers["price_history"] == original
```

- [ ] **Step 2: Run the test to confirm the first case fails**

Run: `.venv/bin/python -m pytest tests/unit/data/test_set_active_provider_validation.py -v`
Expected: `test_set_active_provider_raises_on_unknown_provider_name` FAILS — current implementation silently sets the config to `"nonexistent_provider_xyz"`. The other two should PASS.

- [ ] **Step 3: Add the validation guard to `set_active_provider`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py` — inside `set_active_provider`, immediately after the existing `if domain not in DOMAINS:` block, add:

```python
    # Provider name must correspond to a real @register'd entry for this
    # domain. Silent acceptance defers the failure to the next dispatch,
    # which surfaces as a confusing KeyError far from the swap call site
    # (audit A-041).
    if (domain, name) not in _REGISTRY:
        registered = sorted(n for d, n in _REGISTRY if d == domain)
        raise ValueError(
            f"no provider registered for ({domain!r}, {name!r}); "
            f"registered providers for {domain!r}: {registered}"
        )
```

- [ ] **Step 4: Re-run the test, confirm all three pass**

Run: `.venv/bin/python -m pytest tests/unit/data/test_set_active_provider_validation.py -v`
Expected: 3 PASSED.

- [ ] **Step 5: Run the full suite — catches any callers that depended on the silent acceptance**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS. If anything breaks, it is most likely a backtest fixture passing a stale provider name — fix the caller, not the guard.

- [ ] **Step 6: Commit**

```bash
git add -- src/data/registry.py tests/unit/data/test_set_active_provider_validation.py
git commit -m "feat(data): set_active_provider raises on unknown provider name (A-041)"
```

---

### Task 9: Update `config/README.md` to match the surviving domain set

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/config/README.md`

This is mandatory per project convention — `config/README.md` is the canonical
description of every config setting. Stale rows in the README are exactly the
silent-failure class we are fighting.

- [ ] **Step 1: Delete the rows for removed domains**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/config/README.md` and remove these table rows from the `## data.json — data-provider shell` section:
- `providers.earnings`
- `providers.analyst_consensus`
- `providers.short_interest`
- `providers.options`

- [ ] **Step 2: Update the surviving rows that mention removed providers**

- `providers.company_ratios` row currently reads `(active: pit_composite, fallback: yfinance)`. Replace with `(active: pit_composite)` — there is no fallback after Task 7.
- `providers.news` row currently reads `(active: finnhub, fallback: alpha_vantage)`. Replace with `(active: finnhub, fallback: tiingo)` — alpha_vantage is gone after Task 6.

- [ ] **Step 3: Update the "Phase 6 notes" narrative**

In the same section, the Phase-6 bullet describing the `alpha_vantage → finnhub` swap references alpha_vantage in present tense. Rewrite it as historical context with a closing sentence: "`alpha_vantage` was removed entirely in the 2026-05-26 data-provider cull (audit A-037); `tiingo` remains the registered fallback shell." Keep British spelling throughout (e.g. `behaviour`, `optimise`).

- [ ] **Step 4: Confirm no orphaned references to deleted providers/domains remain in `config/`**

Run: `grep -n "earnings\|analyst_consensus\|short_interest\|options\|alpha_vantage" /home/oscarhill2012/Documents/Repository/StockBot/config/README.md`
Expected: zero matches in the `data.json` section; any matches must be unrelated subjects (e.g. an analyst-config row that happens to mention "options trading" — unlikely).

- [ ] **Step 5: Commit**

```bash
git add -- config/README.md
git commit -m "docs(config): update README for data-provider cull (A-036, A-037, A-038)"
```

---

### Task 10: Delete memory DI setter shells (A-029)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/memory/embeddings.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/memory/compress.py`
- Possibly modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/test_embeddings.py`, `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/test_memory_compress.py` (only if they import the deleted setters)

The audit confirms zero callers of `set_embedding_provider` and
`set_compress_llm`; tests use `monkeypatch` directly on `_default_embed`. The
setters and module-level provider globals are pure dead weight.

- [ ] **Step 1: Reference-scan for setter calls**

Run: `grep -rn "set_embedding_provider\|set_compress_llm\|_embedding_provider\|_compress_llm" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests 2>/dev/null | grep -v __pycache__`
Expected: matches only in the two source files. If a test calls the setter, that test must be updated to `monkeypatch.setattr(...)` instead, then the setter deleted.

- [ ] **Step 2: Collapse `embeddings.py`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/memory/embeddings.py`:
- Delete the `_embedding_provider = None` module global.
- Delete the `set_embedding_provider` function entirely.
- Collapse `embed()` to:

```python
async def embed(text: str) -> list[float]:
    """Embed text using the configured Vertex AI embedding model.

    Delegates to :func:`_default_embed`, which reads the model ID from
    ``config/models.json::memory_embedding`` via
    :func:`src.config.models.get_models_config` — see the docstring of
    ``src/config/models.py`` for the "module owns its own slot" rationale.
    Tests stub this by monkeypatching :func:`_default_embed` directly.
    """
    return await _default_embed(text)
```

- [ ] **Step 3: Collapse `compress.py`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/memory/compress.py`:
- Delete the `_compress_llm = None` module global.
- Delete `set_compress_llm`.
- Inspect the body of `compress()` for any `_compress_llm`-fallback branch and replace it with the explicit `llm_fn` parameter the function already accepts. If `compress()` previously preferred the module global over the parameter, flip it so the parameter is the only path; tests pass `llm_fn=` directly.

- [ ] **Step 4: Run targeted memory tests**

Run: `.venv/bin/python -m pytest tests/unit/test_embeddings.py tests/unit/test_memory_compress.py -v`
Expected: PASS — tests use `monkeypatch` per the audit.

- [ ] **Step 5: Full suite + ruff**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -- src/agents/memory/embeddings.py src/agents/memory/compress.py
git commit -m "chore(memory): delete unused DI setter shells (A-029)"
```

---

### Task 11: Drop the `ImportError` guard around `pydantic` in `llm_retry.py` (A-032)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/llm_retry.py`

Pydantic is a hard dep. The current `try/except ImportError: return False` /
`return None` pattern silently downgrades every schema-validation error to
"not retryable", which means malformed LLM JSON never triggers the schema-
correction retry path it was designed for.

- [ ] **Step 1: Write a regression test proving the current behaviour is wrong**

Add to `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/agents/test_llm_retry.py` (create the file if it does not exist; if it does, append the new tests):

```python
"""llm_retry._is_schema_error must classify pydantic ValidationError as retryable.

Audit A-032: the previous implementation wrapped the pydantic import in
``try/except ImportError: return False``, which silently downgraded every
real schema error to "not retryable" because the import was never going to
fail anyway (pydantic is a hard project dependency).
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from agents.llm_retry import _find_validation_error, _is_schema_error


class _Schema(BaseModel):
    """Trivial model used to provoke a real ValidationError."""

    n: int


def _make_validation_error() -> ValidationError:
    """Return a real pydantic ValidationError instance."""

    try:
        _Schema(n="not an int")  # type: ignore[arg-type]
    except ValidationError as ve:
        return ve
    raise AssertionError("expected ValidationError")


def test_is_schema_error_returns_true_for_pydantic_validation_error():
    assert _is_schema_error(_make_validation_error()) is True


def test_find_validation_error_returns_the_underlying_pydantic_error():
    ve = _make_validation_error()
    assert _find_validation_error(ve) is ve
```

- [ ] **Step 2: Run the new tests against the existing implementation**

Run: `.venv/bin/python -m pytest tests/unit/agents/test_llm_retry.py -v`
Expected: PASS (the guards currently return correctly for the happy path — the bug is the silent-on-failure mode). Document the result; if the test passes against current code, that's fine — we're keeping the test as a regression guard while removing the silent-failure branch.

- [ ] **Step 3: Drop the guards**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/llm_retry.py`:

In `_is_schema_error` (around lines 165-182), replace:
```python
    try:
        from pydantic import ValidationError

        if isinstance(exc, ValidationError):
            return True

    except ImportError:
        return False
```
with an unconditional top-of-module import (move `from pydantic import ValidationError` to the import block at the top of the file) and:
```python
    if isinstance(exc, ValidationError):
        return True
```

Apply the symmetric change to `_find_validation_error` (around lines 206-211): drop its inner `try/except ImportError: return None` and rely on the now-top-level import.

The unused docstring sentence about `_is_rate_limit`'s "import-guard style" should be removed too — it is now a stale rationale.

- [ ] **Step 4: Re-run tests + ruff**

Run: `.venv/bin/python -m pytest tests/ -q && .venv/bin/python -m ruff check src/`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -- src/agents/llm_retry.py tests/unit/agents/test_llm_retry.py
git commit -m "refactor(llm-retry): drop pydantic ImportError guard — hard dep (A-032)"
```

---

### Task 12: Delete `resolve_broker_call` + its four tests (A-064)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/_verb_dispatch.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/agents/executor/test_verb_dispatch.py`
- Modify (docstring only): `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py` (the `resolve_broker_call` mention in a comment at line 72 needs rewording — see Step 2)

The audit confirms zero production callers. Tests cover the function but the
function is never invoked by `agent.py` — the Executor builds its `Order`
directly from `final_orders`. Deletion removes both the function and its
four matching unit tests.

- [ ] **Step 1: Re-confirm zero production callers immediately before deletion**

Run: `grep -rn "resolve_broker_call" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/scripts 2>/dev/null | grep -v __pycache__`
Expected: matches only in `src/agents/executor/_verb_dispatch.py` (the definition) and `src/agents/risk_gate/agent.py:72` (a comment). STOP if any real call site appears.

- [ ] **Step 2: Reword the comment in `risk_gate/agent.py`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/risk_gate/agent.py` line ~72. The current comment says e.g. `# skip broker dispatch for them (resolve_broker_call returns …)`. Rewrite to describe the actual mechanism in the executor (it skips dispatch when the verb is in `_NO_TRADE_INTENTS`). No behaviour change.

- [ ] **Step 3: Delete `resolve_broker_call` from `_verb_dispatch.py`**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/executor/_verb_dispatch.py` and remove the entire `resolve_broker_call` function (currently lines 84-141). If `_NO_TRADE_INTENTS` was used **only** by this function, delete it too; if it is used by other helpers in the file or elsewhere, keep it. Verify with:

Run: `grep -rn "_NO_TRADE_INTENTS\|HALLUCINATED" /home/oscarhill2012/Documents/Repository/StockBot/src 2>/dev/null | grep -v __pycache__`

- [ ] **Step 4: Delete the four tests + the import line**

Edit `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/agents/executor/test_verb_dispatch.py` and remove:
- The `resolve_broker_call` name from the import line at the top of the file (keep `apply_stance_to_thesis`).
- All four test functions named `test_resolve_broker_call_*`.
- Any helper fixtures only used by those four tests.

- [ ] **Step 5: Test + ruff**

Run: `.venv/bin/python -m pytest tests/unit/agents/executor/ tests/integration/test_executor_with_fake_broker.py -v && .venv/bin/python -m ruff check src/ tests/`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -- src/agents/executor/_verb_dispatch.py src/agents/risk_gate/agent.py tests/unit/agents/executor/test_verb_dispatch.py
git commit -m "chore(executor): delete resolve_broker_call — zero production callers (A-064)"
```

---

### Task 13: Final verification

**Files:** none

- [ ] **Step 1: Full test suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS.

- [ ] **Step 2: Lint**

Run: `.venv/bin/python -m ruff check src/ tests/ scripts/`
Expected: PASS.

- [ ] **Step 3: Re-print the active provider set and diff against the Task 1 baseline**

Run: `.venv/bin/python -c "from src.data.config import get_config; import json; print(json.dumps(get_config().providers, indent=2))"`
Expected: 8 entries (price_history, company_ratios, news, social_sentiment, insider_trades, politician_trades, notable_holders, filings) — the four cull-target domains gone.

- [ ] **Step 4: Confirm `DOMAINS` and `DOMAIN_SHAPES` agree**

Run: `.venv/bin/python -c "from src.data.registry import DOMAINS, DOMAIN_SHAPES; assert set(DOMAINS) == set(DOMAIN_SHAPES.keys()), (DOMAINS, DOMAIN_SHAPES.keys()); print('OK', sorted(DOMAINS))"`
Expected: prints `OK ['company_ratios', 'filings', 'insider_trades', 'news', 'notable_holders', 'politician_trades', 'price_history', 'social_sentiment']`.

- [ ] **Step 5: Confirm `set_active_provider` validation in a one-liner**

Run: `.venv/bin/python -c "import data.providers; from data.registry import set_active_provider; \
import pytest; \
print('guards unknown name:', end=' '); \
try: set_active_provider('news', 'bogus'); print('FAIL — accepted unknown')
except ValueError as e: print('OK —', str(e)[:80])"`
Expected: `guards unknown name: OK — no provider registered for 'news', 'bogus'; …`.

---

## Test strategy

**Pre-existing coverage we rely on:**

- `tests/contract/test_provider_shapes.py` iterates `DOMAIN_SHAPES` and
  validates each `(live, cache)` pair returns the canonical shape. After the
  cull this matrix shrinks by 4 domains; the test should still PASS without
  edits because it discovers the matrix from the registry dynamically.
- `tests/unit/data/providers/` — per-provider tests for everything we keep.
  None of the surviving provider tests references the deleted symbols.
- `tests/unit/test_embeddings.py`, `tests/unit/test_memory_compress.py` —
  these use `monkeypatch` per the audit. They should not require edits unless
  they explicitly call the deleted setter; Step 1 of Task 10 catches that.
- Executor coverage (`tests/integration/test_executor_with_fake_broker.py`,
  `tests/unit/agents/executor/`) exercises the live broker-dispatch path that
  `resolve_broker_call` was never on.

**New coverage we add:**

- `tests/unit/data/test_set_active_provider_validation.py` (Task 8, new file)
  — three tests:
  1. unknown provider name → `ValueError` (proves A-041 is fixed);
  2. unknown domain → `ValueError` (regression guard for the pre-existing branch);
  3. registered pair round-trips through swap + restore (happy path).
- `tests/unit/agents/test_llm_retry.py` (Task 11, new file or appended)
  — two tests proving `_is_schema_error` and `_find_validation_error` still
  correctly classify real `pydantic.ValidationError` instances. These are
  regression guards: they were passing before, but the silent-failure branch
  meant the function would have lied on a hypothetical `ImportError` — we
  want the guards to catch any future regression where someone re-adds a
  silent downgrade.

**Tests we delete:**

- `tests/unit/data/providers/test_options_yfinance_shell.py` (Task 2)
- `tests/unit/data/providers/test_short_interest_finra_as_of.py` (Task 3)
- `tests/unit/data/providers/test_analyst_consensus_yfinance.py` (Task 4)
- `tests/unit/data/providers/test_earnings_finnhub_as_of.py` (Task 5)
- `tests/unit/data/providers/test_news_alpha_vantage_as_of.py` (Task 6)
- The four `test_resolve_broker_call_*` functions in
  `tests/unit/agents/executor/test_verb_dispatch.py` (Task 12)

**Tests we explicitly do NOT touch:**

- Any test under `tests/agents/analysts/` — the `agents/analysts/` package is
  the LLM analyst agents, **not** the deleted `analyst_consensus` data
  domain. They are unrelated.
- The `politician_trades` test suite — both `fmp` and `quiver` registrations
  stay; `politician_trades` is intentionally disabled in `_build_provider_fns`
  per project memory, and we do not reactivate it.

---

## Risks / silent-regression checklist

These are the failure modes we are actively guarding against. Each task's
"reference-scan" step is the primary defence; this section enumerates them
so a reviewer can verify nothing slipped through.

1. **Analyst silently importing a deleted provider.** Mitigation: every
   `Task N · Step 1` runs a `grep` for the deleted symbol(s) across `src/`,
   `tests/`, `scripts/`. Each task explicitly STOPS if any analyst,
   extractor, or backtest provider hits.
2. **`config/data.json` retains a dangling domain entry.** Mitigation: each
   cull task removes the matching JSON entry in the same commit. Task 13
   re-loads the config and asserts the surviving set is exactly 8 domains.
3. **`DOMAINS` and `DOMAIN_SHAPES` drift.** Mitigation: Task 13 Step 4
   asserts `set(DOMAINS) == set(DOMAIN_SHAPES.keys())` programmatically.
4. **Provider-shape contract test breaks.** Mitigation: the contract test
   reads `DOMAIN_SHAPES` dynamically. If a deleted domain leaves a row in
   the table, the test fails loud at the next run.
5. **Backtest cache providers (`src/backtest/providers/*_cache.py`)
   reference a deleted domain.** Mitigation: pre-cull grep already confirms
   no `*_cache.py` exists for `earnings`, `analyst_consensus`,
   `short_interest`, or `options`. Task 13 full-suite re-runs the backtest
   integration smoke tests.
6. **`scripts/backtest_fetch.py::_build_provider_fns` references a deleted
   domain.** Mitigation: pre-cull grep on the fetcher already confirms it
   does not enumerate the deleted domains. Task 13 full-suite catches any
   late binding.
7. **`set_active_provider` guard is too tight and breaks a legitimate swap.**
   Mitigation: Task 8 Step 5 runs the full suite immediately after adding
   the guard. The backtest runner is the only legitimate caller; it swaps
   live domains to `"cache"`, and every surviving domain has a `cache`
   provider via the registry import dance in `data.providers.__init__`.
8. **`alpha_vantage` removal silently breaks the news fallback path.**
   Mitigation: after Task 6, `news` has `finnhub` (active) and `tiingo`
   (fallback shell) — both registered. The contract is preserved.
9. **`stats/yfinance.py` accidentally loses its `price_history` registration
   while deleting the `company_ratios` duplicate.** Mitigation: Task 7
   targets only the second `@register` block by domain name match;
   `price_history` registration is a separate decorator earlier in the
   file. Full suite at Task 7 Step 4 catches any over-zealous deletion.
10. **`_NO_TRADE_INTENTS` / `HALLUCINATED` sentinel in
    `_verb_dispatch.py` becomes dead after removing `resolve_broker_call`.**
    Mitigation: Task 12 Step 3 explicitly greps before deleting either
    symbol — kept if any other caller exists.
11. **`config/README.md` retains stale references to deleted providers.**
    Mitigation: Task 9 Step 4 greps the README for every deleted-provider
    name in the `data.json` section.
12. **`politician_trades` is intentionally disabled per project memory.**
    Mitigation: we do not touch it. Both `fmp` and `quiver` registrations
    stay; the commented-out line in `_build_provider_fns` stays commented.

---

## Definition of done

- [ ] All 13 tasks complete, each as its own commit, in order.
- [ ] `DOMAINS` and `DOMAIN_SHAPES` in `src/data/registry.py` both contain
      exactly the eight surviving domains; the assertion in Task 13 Step 4
      passes.
- [ ] `config/data.json` `providers` block contains exactly eight entries
      matching the surviving domains.
- [ ] `config/README.md` `## data.json` section contains rows for those eight
      domains only, with corrected fallback annotations for `company_ratios`
      and `news`, and the rewritten Phase-6 narrative.
- [ ] `set_active_provider` raises `ValueError` on an unregistered
      `(domain, name)` pair; the three tests in
      `tests/unit/data/test_set_active_provider_validation.py` pass.
- [ ] `_is_schema_error` and `_find_validation_error` import `pydantic`
      unconditionally; the two new regression tests pass.
- [ ] `resolve_broker_call` and its four tests are gone; the executor
      integration tests still pass.
- [ ] `set_embedding_provider` / `set_compress_llm` and their module
      globals are gone; `tests/unit/test_embeddings.py` and
      `tests/unit/test_memory_compress.py` pass.
- [ ] `company_ratios.yfinance` duplicate `@register` block is gone from
      `stats/yfinance.py`; `price_history` registration is intact.
- [ ] Full `pytest tests/` and `ruff check src/ tests/ scripts/` both pass.
- [ ] No file under `src/`, `tests/`, `scripts/`, or `config/` references
      `OptionContract`, `EarningsHistory`, `EarningsReport`,
      `AnalystConsensusBundle`, `AnalystRating`, `AnalystRevision`,
      `ShortInterestSnapshot`, `resolve_broker_call`,
      `set_embedding_provider`, or `set_compress_llm`.
