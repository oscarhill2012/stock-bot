# Plan 09 — Reference-symbols + Analyst Singletons + Schema Helpers

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse four pieces of accidentally-duplicated configuration into single sources of truth — the `_REFERENCE_SYMBOLS` tuple, the deterministic-analyst module-level singletons, the `DOMAINS` frozenset, and the `schema_cap` helper — and inline the one-caller `_dispatch_app_name` shim.

**Architecture:** Each consolidation introduces one canonical home (a leaf module with no project imports), migrates call sites to import from that home, and deletes the duplicates. The deterministic-analyst case differs: it deletes module-level singletons outright in favour of the existing `_build_*_analyst` factories — the singletons are an anti-pattern (they execute file I/O at import time via `load_heuristics()`), and the factory path already exists. Failures during consolidation must **raise**, not silently fall back to a fresh instance or empty value.

**Tech Stack:** Python 3.12, pytest, Google ADK BaseAgent, Pydantic.

---

## 0. Trust contract

**Plan 09 trusts:**
- **Plan 03 (state-helper consolidation)** has landed or is landing in parallel. Plan 09 does not touch state helpers, but the consolidation pattern (single source of truth, no compatibility shim) is identical.

**Plan 09 is disjoint from:** Plans 02, 04–08. May run in parallel.

**Later plans trust Plan 09 to land:**
- **Plan 11 (test consolidation)** will write fixture code against `_build_technical_analyst` / `_build_social_analyst` and rely on `technical_analyst` / `social_analyst` module attributes being gone. If Plan 11 still finds those names, the symbol-deletion step here was skipped.
- All downstream plans assume `_REFERENCE_SYMBOLS`, `DOMAINS`, and `schema_cap` each have exactly one definition site and that any new module importing them does so from the canonical home.

**Cross-cutting rules (re-stated for executor):**
- No backwards-compat shims (no re-export from the old location, no aliasing).
- British English throughout new code, comments, and docs.
- Failures during construction (e.g. heuristics-loader can't read its JSON) must **raise**. No `try / except` fallback that returns a default instance — that masks misconfiguration exactly the way singletons did.

---

## 1. Consolidation map

For each constant or helper: today's definition sites and call sites, and tomorrow's single source of truth.

### 1.1 `_REFERENCE_SYMBOLS` (A-035)

**Today (three definitions, two call sites):**
- `src/orchestrator/tick.py:62` — defines the 12-symbol tuple; consumed at `:135` by `_fetch_reference_prices`.
- `scripts/backtest_fetch.py:379` — re-defines the same 12-symbol tuple verbatim; consumed at `:417` by `_fill_reference_ohlcv`.
- `tests/unit/orchestrator/test_tick_reference_prices.py:15` — third copy used to build the fake price-history dict in two tests.
- `src/backtest/runner.py:118` — imports `_REFERENCE_SYMBOLS` from `scripts.backtest_fetch` (this is the only correct cross-module reference today).

**Tomorrow (one definition):**
- `src/data/reference_symbols.py` — new leaf module exposing `REFERENCE_SYMBOLS: tuple[str, ...]`. No project imports (only `__future__`). Sits in `src/data/` because it is data-domain knowledge (sector ETFs as market-reference instruments), not orchestrator policy. Public name (no leading underscore) because four modules in three packages need to import it.

### 1.2 Deterministic-analyst singletons (A-074)

**Today (two singletons + two factories side by side):**
- `src/agents/analysts/technical/agent.py:159` — `technical_analyst = TechnicalAnalyst(heuristics=load_heuristics().technical)` runs at import time.
- `src/agents/analysts/technical/agent.py:162-174` — `_build_technical_analyst(heuristics=None)` factory exists alongside the singleton.
- `src/agents/analysts/technical/__init__.py` — re-exports `technical_analyst`.
- `src/agents/analysts/social/agent.py:142` — `social_analyst = SocialAnalyst(heuristics=load_heuristics().social)`.
- `src/agents/analysts/social/agent.py:145-157` — `_build_social_analyst` factory.
- `src/agents/analysts/social/__init__.py` — no re-export (module-level docstring only).
- **Production call sites** (already use the factories): `src/orchestrator/pipeline.py:52-67`.
- **Test call sites** (use the singletons): `tests/analysts/test_technical.py:10,15,19` — two assertions only.

**Tomorrow (factories only):**
- Singletons deleted from both agent modules.
- `src/agents/analysts/technical/__init__.py` re-export deleted (file becomes a docstring-only `__init__.py` like the social one).
- The two assertions in `tests/analysts/test_technical.py` rewritten to call `_build_technical_analyst()`.

### 1.3 `DOMAINS` frozenset (A-075)

**Today (two definitions):**
- `src/data/registry.py:101` — public `DOMAINS: frozenset[str]`.
- `src/data/config.py:18` — `_DOMAINS: frozenset[str]`, manually kept in sync by comments. Used at `:59,:62` inside `DataConfig._check_domains` to validate the loaded JSON has exactly the expected providers.

The duplication exists because `data.config` is imported by `data.registry` (well, by code that the registry depends on), so importing `DOMAINS` from `registry` into `config` would risk a cycle.

**Tomorrow (one definition, dependency inverted):**
- `src/data/domains.py` — new leaf module exposing `DOMAINS: frozenset[str]`. No project imports. Both `data.config` and `data.registry` import from it. Eliminates the comment-enforced sync.

### 1.4 `schema_cap` helper (A-076)

**Today (two identical implementations):**
- `src/config/analysts.py:187-207` — `AnalystsConfig.schema_cap(self, prompt_cap) -> int`.
- `src/config/strategist.py:152-174` — `StrategistConfig.schema_cap(self, prompt_cap) -> int`. The body is line-for-line identical; the docstrings say so explicitly ("Mirror of `StrategistConfig.schema_cap`").

Both call sites pass `slack_percent` from `self`. A free function with signature `apply_slack(prompt_cap: int, slack_percent: int) -> int` covers both.

**Tomorrow (one definition):**
- `src/config/_slack.py` — new leaf module exposing `apply_slack(prompt_cap: int, slack_percent: int) -> int`. Both `AnalystsConfig.schema_cap` and `StrategistConfig.schema_cap` become two-line delegations: `return apply_slack(prompt_cap, self.slack_percent)`. The method names stay (call sites in `src/agents/strategist/schema.py`, `src/contract/evidence.py`, four test files) — only the body is shared.

### 1.5 `_dispatch_app_name` (A-088)

**Today (helper with one caller):**
- `src/orchestrator/tick.py:25-54` — 30-line function (mostly docstring) with `match / case`.
- Called once at `src/orchestrator/tick.py:227`.

**Tomorrow (inlined):**
- The two-element match becomes inline lookup at the call site. The docstring's content (paper/live partitioning of `user_state`) moves to a comment above the inline expression. The `ValueError` for unknown modes is unreachable because `_broker_mode` is already constrained to enum members two lines above — verify and drop it.

---

## 2. Ordered changes (file-by-file)

The order matters: introduce each canonical home first, migrate call sites, then delete duplicates. This keeps the test suite green at every commit.

All paths absolute. Project root: `/home/oscarhill2012/Documents/Repository/StockBot`.

---

### Task 1 — Create `src/data/reference_symbols.py`

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/reference_symbols.py`
- Test: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/test_reference_symbols.py`

- [ ] **Step 1: Write the failing test**

```python
"""Canonical reference-symbol tuple lives in one module only."""
from data.reference_symbols import REFERENCE_SYMBOLS


def test_reference_symbols_contains_spy_and_eleven_sector_etfs():
    """SPY plus 11 SPDR sector ETFs — 12 symbols total, ordered deterministically."""
    assert REFERENCE_SYMBOLS[0] == "SPY"                              # broad-market benchmark first
    assert len(REFERENCE_SYMBOLS) == 12                               # SPY + 11 SPDR sector ETFs
    assert set(REFERENCE_SYMBOLS[1:]) == {
        "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
        "XLI", "XLB", "XLRE", "XLU", "XLC",
    }


def test_reference_symbols_is_an_immutable_tuple():
    """Tuple — not list — so call sites cannot accidentally mutate the canonical order."""
    assert isinstance(REFERENCE_SYMBOLS, tuple)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_reference_symbols.py -v`
Expected: `ModuleNotFoundError: No module named 'data.reference_symbols'`.

- [ ] **Step 3: Write the module**

```python
"""Canonical reference-symbol tuple — SPY plus the 11 SPDR sector ETFs.

These symbols are fetched once per tick (live) and once per backtest window
(backtest) as market and sector benchmarks.  They are NOT in the watchlist;
they exist solely so the technical extractor can compute
``relative_strength_vs_spy_*`` and ``relative_strength_vs_sector_*`` features
without issuing per-ticker network calls.

Single source of truth — previously duplicated across ``orchestrator.tick``,
``scripts.backtest_fetch``, and ``backtest.runner``.  Any new consumer must
import from here.
"""
from __future__ import annotations


# SPY is the broad-market benchmark; the 11 SPDR sector ETFs cover every
# S&P 500 constituent sector.  Order is deterministic so tests can compare
# against a fixed expected list.
REFERENCE_SYMBOLS: tuple[str, ...] = (
    "SPY",                                                  # broad-market benchmark
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",              # SPDR sector ETFs (batch 1)
    "XLI", "XLB", "XLRE", "XLU", "XLC",                     # SPDR sector ETFs (batch 2)
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_reference_symbols.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add src/data/reference_symbols.py tests/unit/data/test_reference_symbols.py
git commit -m "feat(data): add canonical REFERENCE_SYMBOLS module"
```

---

### Task 2 — Migrate `src/orchestrator/tick.py` to import `REFERENCE_SYMBOLS`

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/tick.py` (delete lines 56-66, add import, update line 135)

- [ ] **Step 1: Add the import near the top of the file**

After the existing `from datetime import ...` block (around line 7), add:

```python
from data.reference_symbols import REFERENCE_SYMBOLS
```

- [ ] **Step 2: Delete the local `_REFERENCE_SYMBOLS` definition (lines 56-66 inclusive — the explanatory comment block and the tuple).**

- [ ] **Step 3: Replace the one call-site usage at `src/orchestrator/tick.py:135`**

```python
# was:  _REFERENCE_SYMBOLS, as_of=date.today(),
# now:  REFERENCE_SYMBOLS, as_of=date.today(),
```

Also update the docstring reference at line 86 (search for `_REFERENCE_SYMBOLS` and rename to `REFERENCE_SYMBOLS`).

- [ ] **Step 4: Run the orchestrator unit tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/ tests/unit/test_tick_entrypoint.py -v`
Expected: all passing tests stay green. `test_tick_reference_prices.py` will still pass because it has its own local copy (deleted in Task 4).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/tick.py
git commit -m "refactor(orchestrator): import REFERENCE_SYMBOLS from data leaf module"
```

---

### Task 3 — Migrate `scripts/backtest_fetch.py` and `src/backtest/runner.py`

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/scripts/backtest_fetch.py` (delete lines 372-383, update line 417)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/backtest/runner.py` (lines 116-122)

- [ ] **Step 1: In `scripts/backtest_fetch.py`, add the import near the top of the file**

```python
from data.reference_symbols import REFERENCE_SYMBOLS
```

- [ ] **Step 2: Delete lines 372-383 (the comment-block and `_REFERENCE_SYMBOLS` tuple).**

- [ ] **Step 3: Update line 417**

```python
# was:  for symbol in _REFERENCE_SYMBOLS:
# now:  for symbol in REFERENCE_SYMBOLS:
```

- [ ] **Step 4: In `src/backtest/runner.py`, replace lines 116-118**

```python
# was:
#     # Import the canonical reference-symbol list from the fetch script so
#     # the two lists can never drift apart.
#     from scripts.backtest_fetch import _REFERENCE_SYMBOLS
# now:
    from data.reference_symbols import REFERENCE_SYMBOLS
```

- [ ] **Step 5: Update line 122**

```python
# was:  for symbol in _REFERENCE_SYMBOLS:
# now:  for symbol in REFERENCE_SYMBOLS:
```

- [ ] **Step 6: Run the backtest unit + integration smoke**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/backtest/ -v`
Expected: green.

- [ ] **Step 7: Commit**

```bash
git add scripts/backtest_fetch.py src/backtest/runner.py
git commit -m "refactor(backtest): import REFERENCE_SYMBOLS from data leaf module"
```

---

### Task 4 — Rewrite `tests/unit/orchestrator/test_tick_reference_prices.py` to use the canonical tuple

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/orchestrator/test_tick_reference_prices.py`

- [ ] **Step 1: Replace the local `_REFERENCE_SYMBOLS` tuple at lines 15-27 with an import**

```python
# was: local copy of the tuple
from data.reference_symbols import REFERENCE_SYMBOLS as _REFERENCE_SYMBOLS
```

Keep the local alias `_REFERENCE_SYMBOLS` so the two `for sym in _REFERENCE_SYMBOLS` loops at lines 28 and 55 do not need editing. (We are not aliasing for compat — this is a within-test convention only; the public canonical name is what production code uses.)

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/test_tick_reference_prices.py -v`
Expected: green.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/orchestrator/test_tick_reference_prices.py
git commit -m "test(orchestrator): import REFERENCE_SYMBOLS from data leaf module"
```

---

### Task 5 — Absence test: no module re-defines `_REFERENCE_SYMBOLS`

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/tests/contract/test_no_duplicate_reference_symbols.py`

- [ ] **Step 1: Write the test**

```python
"""Tripwire — only ``data.reference_symbols`` may define ``REFERENCE_SYMBOLS``.

Catches regressions where a future engineer reintroduces a local tuple to
"avoid the import" — exactly what audit A-035 found three times.
"""
from __future__ import annotations

import re
from pathlib import Path


_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_only_one_reference_symbols_definition_in_repo():
    """Grep src/ and scripts/ for any literal ``REFERENCE_SYMBOLS\\s*[:=]`` — the
    canonical module is the only allowed hit. Excludes tests/ (aliases allowed)."""

    # Match the canonical definition pattern: `REFERENCE_SYMBOLS:` (annotated)
    # or `REFERENCE_SYMBOLS =` (bare). The leading-underscore form is included
    # because the audit found three `_REFERENCE_SYMBOLS = ...` definitions.
    pattern = re.compile(r"\b_?REFERENCE_SYMBOLS\s*[:=]")

    hits: list[Path] = []
    for sub in ("src", "scripts"):
        for path in (_PROJECT_ROOT / sub).rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(path.relative_to(_PROJECT_ROOT))

    canonical = Path("src/data/reference_symbols.py")
    assert hits == [canonical], (
        f"REFERENCE_SYMBOLS must be defined only in {canonical}; "
        f"also found in: {[str(h) for h in hits if h != canonical]}"
    )
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_no_duplicate_reference_symbols.py -v`
Expected: PASS (Tasks 2–3 already deleted the duplicates).

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_no_duplicate_reference_symbols.py
git commit -m "test(contract): tripwire — REFERENCE_SYMBOLS defined in one place"
```

---

### Task 6 — Create `src/data/domains.py` and rewire `data.config` + `data.registry`

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/domains.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/config.py` (lines 14-31, 59, 62)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/data/registry.py` (lines 101-116)
- Test: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/data/test_domains.py`

- [ ] **Step 1: Write the failing test**

```python
"""Canonical DOMAINS frozenset lives in one module only."""
from data.domains import DOMAINS


def test_domains_contains_expected_twelve_domains():
    """Twelve domains — eight Phase-1/2 plus four Phase-3 additions."""
    assert DOMAINS == frozenset({
        "price_history", "company_ratios", "news", "social_sentiment",
        "insider_trades", "politician_trades", "notable_holders", "filings",
        "earnings", "analyst_consensus", "short_interest", "options",
    })


def test_data_config_and_data_registry_use_the_same_object():
    """No accidental drift between the two consumers — both must read from the leaf."""
    from data.config import _DOMAINS as config_domains       # internal alias inside config
    from data.registry import DOMAINS as registry_domains
    assert config_domains is registry_domains is DOMAINS
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/test_domains.py -v`
Expected: `ModuleNotFoundError: No module named 'data.domains'`.

- [ ] **Step 3: Write `src/data/domains.py`**

```python
"""Canonical set of data-provider domain names.

Single source of truth — previously duplicated between ``data.config``
(as the private ``_DOMAINS``) and ``data.registry`` (as the public
``DOMAINS``).  The duplication existed to avoid an import cycle: this
leaf module has no project imports and breaks the cycle cleanly.

A domain is a category of data (price history, news, filings, …) for
which exactly one provider must be configured in ``config/data.json``.
"""
from __future__ import annotations


# Twelve domains: eight from Phase 1/2 plus four Phase-3 additions.  Any
# addition here must also gain a ``DOMAIN_SHAPES`` entry in
# ``data.registry`` and a ``providers`` entry in ``config/data.json``.
DOMAINS: frozenset[str] = frozenset({
    # Phase 1/2 — "stats" retired in Phase 5; split into price_history + company_ratios.
    "price_history",
    "company_ratios",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
    # Phase 3 additions.
    "earnings",            # Finnhub earnings calendar / actuals
    "analyst_consensus",   # yfinance analyst ratings aggregation
    "short_interest",      # FINRA short-interest (bi-monthly)
    "options",             # yfinance options chain (live-only shell)
})
```

- [ ] **Step 4: Rewire `src/data/config.py`**

Replace lines 14-31 (the comment block and the `_DOMAINS = frozenset({...})` literal) with:

```python
# Re-export from the canonical leaf so ``DataConfig._check_domains`` and
# the registry validate against the same object.  Underscore prefix kept
# because the alias is module-internal; the public name lives in
# ``data.domains``.
from data.domains import DOMAINS as _DOMAINS
```

Leave the two usages at lines 59 and 62 untouched — they already reference `_DOMAINS`.

- [ ] **Step 5: Rewire `src/data/registry.py`**

Replace lines 101-116 (the comment block and the `DOMAINS = frozenset({...})` literal) with:

```python
from data.domains import DOMAINS  # canonical — see src/data/domains.py
```

Place the import with the other top-of-file imports rather than mid-module.

- [ ] **Step 6: Run targeted tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/ -v`
Expected: green, including the new `test_domains.py`.

- [ ] **Step 7: Run the full data + contract sweep**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/ tests/contract/ -v`
Expected: green.

- [ ] **Step 8: Commit**

```bash
git add src/data/domains.py src/data/config.py src/data/registry.py tests/unit/data/test_domains.py
git commit -m "refactor(data): consolidate DOMAINS into data.domains leaf module"
```

---

### Task 7 — Create `src/config/_slack.py` and collapse both `schema_cap` bodies

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/src/config/_slack.py`
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/config/analysts.py` (lines 187-207)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/config/strategist.py` (lines 152-174)
- Test: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/config/test_apply_slack.py`

- [ ] **Step 1: Write the failing test**

```python
"""``apply_slack`` — single shared implementation of the slack-headroom calc."""
import pytest

from config._slack import apply_slack


def test_apply_slack_uses_integer_math_to_dodge_fp_inconsistency():
    """200 * 1.10 in FP would be 220.00000000000003; integer math must give 220 exactly."""
    assert apply_slack(200, 10) == 220
    assert apply_slack(600, 10) == 660
    assert apply_slack(160, 10) == 176


def test_apply_slack_with_zero_headroom_is_identity():
    assert apply_slack(200, 0) == 200
    assert apply_slack(1, 0) == 1


def test_apply_slack_rounds_up_on_remainder():
    """1 * 1.10 = 1.10 → ceil is 2. The ``+99`` term forces ceiling division."""
    assert apply_slack(1, 10) == 2


def test_apply_slack_rejects_negative_slack():
    """Negative headroom would shrink the cap and silently truncate output."""
    with pytest.raises(ValueError):
        apply_slack(100, -1)
```

- [ ] **Step 2: Run it**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_apply_slack.py -v`
Expected: `ModuleNotFoundError: No module named 'config._slack'`.

- [ ] **Step 3: Write `src/config/_slack.py`**

```python
"""Shared ``apply_slack`` helper — used by both AnalystsConfig and StrategistConfig.

Previously this calculation was implemented twice, line-for-line identical,
in ``config.analysts.AnalystsConfig.schema_cap`` and
``config.strategist.StrategistConfig.schema_cap``.  Single source of truth
lives here; both methods now delegate.
"""
from __future__ import annotations


def apply_slack(prompt_cap: int, slack_percent: int) -> int:
    """Return the schema-enforced ``max_length`` for a prompt-stated cap.

    Adds ``slack_percent`` headroom and rounds up using integer math.  Integer
    math dodges floating-point surprises — ``600 * 1.1`` yields exactly
    ``660.0`` but ``200 * 1.1`` yields ``220.00000000000003`` due to binary
    representation, so the two prompt caps would round inconsistently with
    ``ceil(prompt_cap * 1.1)``.  ``(prompt_cap * (100 + slack) + 99) // 100``
    gives the same answer for both: 200 → 220, 600 → 660.

    Parameters
    ----------
    prompt_cap:
        The cap value the model is told in the prompt template.
    slack_percent:
        Extra headroom (0–100) added to the schema cap so the model has room
        before validation fails.

    Returns
    -------
    int
        ``ceil(prompt_cap * (100 + slack_percent) / 100)`` — the value passed
        to ``Field(max_length=...)``.

    Raises
    ------
    ValueError
        When ``slack_percent`` is negative.  A negative value would shrink
        the schema cap below the prompt cap and silently truncate model
        output — exactly the silent-failure pattern this audit aims to kill.
    """
    if slack_percent < 0:
        raise ValueError(f"slack_percent must be >= 0, got {slack_percent}")
    return (prompt_cap * (100 + slack_percent) + 99) // 100
```

- [ ] **Step 4: Run the new test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/test_apply_slack.py -v`
Expected: 4 passed.

- [ ] **Step 5: Collapse `AnalystsConfig.schema_cap`**

In `src/config/analysts.py`, replace the body of `schema_cap` (lines 187-207) with:

```python
    def schema_cap(self, prompt_cap: int) -> int:
        """Derive the schema-enforced ``max_length`` from a prompt-stated cap.

        Thin delegation to :func:`config._slack.apply_slack` — see that
        function for the integer-math rationale.

        Parameters
        ----------
        prompt_cap:
            The cap value the LLM is told in the prompt template.

        Returns
        -------
        int
            The schema-enforced ``max_length``.
        """
        return apply_slack(prompt_cap, self.slack_percent)
```

Add `from config._slack import apply_slack` to the imports at the top.

- [ ] **Step 6: Collapse `StrategistConfig.schema_cap`**

Same edit in `src/config/strategist.py` (lines 152-174):

```python
    def schema_cap(self, prompt_cap: int) -> int:
        """Derive the schema-enforced ``max_length`` from a prompt-stated cap.

        Thin delegation to :func:`config._slack.apply_slack` — see that
        function for the integer-math rationale.

        Parameters
        ----------
        prompt_cap:
            The cap value the model is told in the prompt template.

        Returns
        -------
        int
            The schema-enforced ``max_length``.
        """
        return apply_slack(prompt_cap, self.slack_percent)
```

Add `from config._slack import apply_slack` to the imports at the top.

- [ ] **Step 7: Run the existing config tests to verify behaviour is unchanged**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/config/ tests/unit/test_strategist_schema.py tests/integration/test_fundamental_canned_output.py tests/unit/agents/strategist/test_stance_schema.py -v`
Expected: green. These tests call `cfg.schema_cap(...)` directly — proves the delegation is transparent.

- [ ] **Step 8: Commit**

```bash
git add src/config/_slack.py src/config/analysts.py src/config/strategist.py tests/unit/config/test_apply_slack.py
git commit -m "refactor(config): collapse schema_cap into shared apply_slack helper"
```

---

### Task 8 — Rewrite the two singleton assertions in `tests/analysts/test_technical.py`

Plan 09 deletes the singletons in Task 9; rewrite the tests **first** so the suite stays green across the deletion.

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/tests/analysts/test_technical.py`

- [ ] **Step 1: Rewrite the two assertions to use the factory**

```python
"""Technical analyst unit tests (Tier 1 — no LLM).

Phase 5 Task 8: TechnicalAnalyst is a BaseAgent subclass (not LlmAgent).
Plan 09 (audit consolidation): the module-level ``technical_analyst``
singleton was deleted; tests now build a fresh instance via the
``_build_technical_analyst`` factory.
"""
from google.adk.agents import BaseAgent

from agents.analysts.technical.agent import _build_technical_analyst


def test_technical_analyst_is_base_agent():
    """TechnicalAnalyst must be a BaseAgent — it has no LLM dependency."""
    analyst = _build_technical_analyst()
    assert isinstance(analyst, BaseAgent)


def test_technical_analyst_name():
    analyst = _build_technical_analyst()
    assert analyst.name == "TechnicalAnalyst"
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/test_technical.py -v`
Expected: green (singleton still exists at this point; both import paths work).

- [ ] **Step 3: Commit**

```bash
git add tests/analysts/test_technical.py
git commit -m "test(analysts): use _build_technical_analyst factory instead of singleton"
```

---

### Task 9 — Delete the deterministic-analyst singletons and the `__init__.py` re-export

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/technical/agent.py` (delete lines 157-159)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/technical/__init__.py` (replace re-export with docstring)
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/social/agent.py` (delete lines 140-142)

- [ ] **Step 1: Delete `technical_analyst` singleton + its two-line comment in `technical/agent.py`**

Remove lines 157-159 (the `# Module-level singleton — used directly by ...` comment and the `technical_analyst = TechnicalAnalyst(...)` assignment).

- [ ] **Step 2: Replace `src/agents/analysts/technical/__init__.py` content**

Was:
```python
from .agent import technical_analyst

__all__ = ["technical_analyst"]
```

Now:
```python
"""Deterministic Technical analyst package (Phase 5).

Mirrors the Social analyst package — see ``agents.analysts.social`` for the
LlmAgent-vs-BaseAgent rationale.  Production callers construct via the
``_build_technical_analyst`` factory in ``agents.analysts.technical.agent``;
there is intentionally no module-level singleton (singletons executed file
I/O at import time and made misconfiguration silent).
"""
```

- [ ] **Step 3: Delete `social_analyst` singleton + comment in `social/agent.py`**

Remove lines 140-142.

- [ ] **Step 4: Run the analyst tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/analysts/ tests/unit/ -v -k "technical or social or analyst"`
Expected: green. (Task 8 already migrated the only test that imported the singleton; if any other test surfaces a broken import, fix it the same way — replace `technical_analyst` with `_build_technical_analyst()` and `social_analyst` with `_build_social_analyst()`.)

- [ ] **Step 5: Full pipeline test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_analyst_pool.py -v`
Expected: green — `_build_analyst_pool` calls the factories directly, never the singletons.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/technical/agent.py src/agents/analysts/technical/__init__.py src/agents/analysts/social/agent.py
git commit -m "refactor(analysts): delete module-level singletons; factories only"
```

---

### Task 10 — Identity + absence tests for the analyst factories

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/tests/contract/test_no_analyst_singletons.py`

- [ ] **Step 1: Write the test**

```python
"""Tripwire — no module-level deterministic-analyst singletons.

Singletons executed file I/O at import time (``load_heuristics()`` reads a
JSON config) and any failure produced a hard-to-trace ImportError far from
the misconfiguration.  Audit A-074 mandates factories only.
"""
from __future__ import annotations

from agents.analysts.social.agent import _build_social_analyst
from agents.analysts.technical.agent import _build_technical_analyst


def test_technical_module_does_not_expose_singleton():
    """Importing the agent module must not bind ``technical_analyst``."""
    from agents.analysts.technical import agent as tech_mod
    assert not hasattr(tech_mod, "technical_analyst"), (
        "technical_analyst singleton was deleted in Plan 09 — use "
        "_build_technical_analyst() instead."
    )


def test_social_module_does_not_expose_singleton():
    """Importing the agent module must not bind ``social_analyst``."""
    from agents.analysts.social import agent as soc_mod
    assert not hasattr(soc_mod, "social_analyst"), (
        "social_analyst singleton was deleted in Plan 09 — use "
        "_build_social_analyst() instead."
    )


def test_technical_package_init_does_not_reexport_singleton():
    """The technical package ``__init__`` was a 2-line re-export — now docstring-only."""
    from agents.analysts import technical
    assert not hasattr(technical, "technical_analyst")


def test_build_technical_analyst_returns_a_fresh_instance_each_call():
    """Factory contract — successive calls return distinct objects (no hidden cache)."""
    a = _build_technical_analyst()
    b = _build_technical_analyst()
    assert a is not b, "factory must build fresh instances, not memoise"
    assert type(a) is type(b)


def test_build_social_analyst_returns_a_fresh_instance_each_call():
    a = _build_social_analyst()
    b = _build_social_analyst()
    assert a is not b
    assert type(a) is type(b)
```

- [ ] **Step 2: Run the test**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/contract/test_no_analyst_singletons.py -v`
Expected: 5 passed.

- [ ] **Step 3: Commit**

```bash
git add tests/contract/test_no_analyst_singletons.py
git commit -m "test(contract): tripwire — no module-level analyst singletons"
```

---

### Task 11 — Inline `_dispatch_app_name` (A-088)

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/tick.py` (delete lines 25-54, rewrite line 227)

- [ ] **Step 1: Verify `_dispatch_app_name` truly has only one caller**

Run: `grep -rn "_dispatch_app_name\|dispatch_app_name" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/tests /home/oscarhill2012/Documents/Repository/StockBot/scripts`
Expected: two hits — the definition at `src/orchestrator/tick.py:25` and the call at `:227`. If any test patches or imports it, abort and add the test rewrite to this task before deleting.

- [ ] **Step 2: Delete the function (lines 25-54 inclusive)**

- [ ] **Step 3: Replace the call site at `src/orchestrator/tick.py:227`**

Was:
```python
    _app_name = _dispatch_app_name(_broker_mode)
```

Now:
```python
    # Partition ADK ``user_state`` rows between paper and live so the two
    # modes cannot share thesis rows.  Backtest uses a third value
    # (``f"StockBot-backtest-{window_key}"``) set in the backtest driver.
    # ``_broker_mode`` is already a ``BrokerMode`` member at this point
    # (the conversion two lines above falls back to PAPER on unknown input),
    # so the ``StrEnum``-style lookup is exhaustive.
    _app_name = f"StockBot-{_broker_mode.value}"
```

- [ ] **Step 4: Run tick tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/orchestrator/ tests/unit/test_tick_entrypoint.py -v`
Expected: green. The two `app_name` values produced (`"StockBot-live"`, `"StockBot-paper"`) are byte-for-byte what `_dispatch_app_name` returned.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/tick.py
git commit -m "refactor(orchestrator): inline single-caller _dispatch_app_name"
```

---

### Task 12 — Full-suite verification

- [ ] **Step 1: Run the entire test suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`
Expected: all green. If any test fails citing one of the deleted names (`_REFERENCE_SYMBOLS` in `scripts.backtest_fetch`, `technical_analyst`, `social_analyst`, `_dispatch_app_name`, `_DOMAINS` in any module other than its config-internal alias), update it to the factory / canonical-import path. Do **not** reintroduce a shim.

- [ ] **Step 2: Run ruff**

Run: `.venv/bin/python -m ruff check src/ scripts/ tests/`
Expected: no new findings introduced by Plan 09.

- [ ] **Step 3: Append a graph_delta entry**

The structural changes (new modules `data.reference_symbols`, `data.domains`, `config._slack`; deleted symbols `technical_analyst`, `social_analyst`, `_dispatch_app_name`, `_REFERENCE_SYMBOLS` in three locations, `_DOMAINS` literal in `data.config`, two `schema_cap` bodies; deleted re-export in `agents/analysts/technical/__init__.py`) all qualify under the project's `graph_delta.md` convention. Append a dated entry summarising the moves.

- [ ] **Step 4: No commit (graph_delta is gitignored).**

---

## 3. Test strategy

Two complementary shapes, repeated for each of the four consolidations:

1. **Behavioural parity** — for `schema_cap` and `apply_slack`, the existing tests in `tests/unit/config/test_analysts_config.py`, `tests/unit/test_strategist_schema.py`, `tests/unit/agents/strategist/test_stance_schema.py`, and `tests/integration/test_fundamental_canned_output.py` already exercise the integer-math behaviour. They pass unchanged after the delegation, which is itself the parity proof. The new `tests/unit/config/test_apply_slack.py` adds the missing `slack_percent < 0` raise (silent-failure prevention).
2. **Identity** — the leaf module is the same object everywhere:
   - `test_domains.py::test_data_config_and_data_registry_use_the_same_object` asserts `config._DOMAINS is registry.DOMAINS is data.domains.DOMAINS`.
   - `test_no_analyst_singletons.py::test_build_*_returns_a_fresh_instance_each_call` asserts factories build fresh objects rather than memoising (otherwise the singleton was just renamed).
3. **Absence (tripwire)** — regex-scan `src/` and `scripts/` to catch reintroduction:
   - `tests/contract/test_no_duplicate_reference_symbols.py` — at most one definition of `REFERENCE_SYMBOLS`.
   - `tests/contract/test_no_analyst_singletons.py` — `agent` modules expose no `technical_analyst` / `social_analyst` attribute, and the `__init__` does not re-export them.

There is no separate "absence" test for `DOMAINS` (the identity test covers it — if anyone redefines `_DOMAINS`, the `is` chain breaks) or for `schema_cap` (the methods stay; only the body collapses, so a regression would require reintroducing the integer-math literal in both classes, which the parity tests would catch on diff review).

There is no test for the `_dispatch_app_name` inline — it has no behaviour change and the existing tick tests already cover both paper and live app-name resolution.

---

## 4. Risks / silent-regression checklist

**Import cycles** — the primary risk. Plan 09 introduces three new leaf modules; each must have **zero project imports** to break the cycle it is replacing.

- [ ] `src/data/reference_symbols.py` — `from __future__ import annotations` only. Verify with `grep -E "^(import|from)" /home/oscarhill2012/Documents/Repository/StockBot/src/data/reference_symbols.py` after Task 1.
- [ ] `src/data/domains.py` — same check.
- [ ] `src/config/_slack.py` — same check.

If any leaf imports from elsewhere in the project, the cycle returns.

**Silent regressions to watch for:**

- [ ] After Task 6, run `PYTHONPATH=src .venv/bin/python -c "from data import get_price_history; from data.config import get_config; print(get_config().providers)"`. If `data.registry` and `data.config` disagree on which domains exist, `DataConfig._check_domains` will now raise immediately on load — exactly the right behaviour, but verify the JSON file currently passes.
- [ ] After Task 9, search for any production code path that imports `technical_analyst` or `social_analyst`: `grep -rn "technical_analyst\|social_analyst" /home/oscarhill2012/Documents/Repository/StockBot/src /home/oscarhill2012/Documents/Repository/StockBot/scripts`. Expected: zero hits in `src/` and `scripts/` (test references are fine post-Task 8 because they were rewritten to use the factory).
- [ ] After Task 7, verify `slack_percent: int = Field(ge=0, le=50, default=10)` is still on both `AnalystsConfig` and `StrategistConfig`. The Pydantic field validator catches negative values at config-load time; the `apply_slack` raise is belt-and-braces for direct callers, but the Pydantic ge=0 is the front-line defence.
- [ ] After Task 11, run a backtest smoke (`PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window <any>`) to confirm the inlined `f"StockBot-{_broker_mode.value}"` produces an identical `app_name` to `_dispatch_app_name`. The previous function returned the literal strings `"StockBot-live"` and `"StockBot-paper"`; the inline f-string must produce byte-for-byte the same values.
- [ ] Heuristics-loader failure mode: confirm `load_heuristics()` raises on missing/malformed JSON rather than returning a default. After Task 9 there is no singleton to mask this, so a bad config now surfaces at the first `_build_*_analyst()` call rather than at module import — which is the intent, but verify by deliberately corrupting the heuristics JSON in a scratch run before deciding the test suite is sufficient.

**No backwards-compat shims:**

- [ ] No `_REFERENCE_SYMBOLS = REFERENCE_SYMBOLS` alias added in `orchestrator.tick`, `backtest.runner`, or `scripts.backtest_fetch`.
- [ ] No `technical_analyst = _build_technical_analyst()` alias added in `agents.analysts.technical.agent` or its `__init__`.
- [ ] No `_DOMAINS` re-export anywhere other than the explicit internal alias inside `data.config` (acknowledged in that file's two usages).

---

## 5. Definition of done

- [ ] `REFERENCE_SYMBOLS` defined once — in `src/data/reference_symbols.py`. Tripwire test green.
- [ ] `DOMAINS` defined once — in `src/data/domains.py`. Identity test confirms `data.config._DOMAINS is data.registry.DOMAINS is data.domains.DOMAINS`.
- [ ] `apply_slack` defined once — in `src/config/_slack.py`. Both `AnalystsConfig.schema_cap` and `StrategistConfig.schema_cap` are two-line delegations. Negative-slack call raises `ValueError`.
- [ ] `technical_analyst` and `social_analyst` module-level singletons are gone from `agents.analysts.technical.agent`, `agents.analysts.social.agent`, and `agents.analysts.technical.__init__`. The two `_build_*_analyst` factories are the sole construction path. Tripwire tests green.
- [ ] `_dispatch_app_name` is gone from `src/orchestrator/tick.py`; the call site uses an inline f-string with the partitioning comment preserved.
- [ ] No new module imports any of the deleted names. `grep -rn` in `src/` and `scripts/` returns zero hits for `_REFERENCE_SYMBOLS`, `technical_analyst`, `social_analyst`, `_dispatch_app_name`.
- [ ] Full suite (`PYTHONPATH=src .venv/bin/python -m pytest tests/ -v`) green.
- [ ] `.venv/bin/python -m ruff check src/ scripts/ tests/` green.
- [ ] `graph_delta.md` updated with a dated entry summarising the three new modules and five deletions.
- [ ] No backwards-compat shims, no `try/except` fallbacks around constructor calls, no silent defaults introduced anywhere in the diff.
