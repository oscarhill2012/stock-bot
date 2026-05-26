# Plan 10 — Backtest Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the duplicated cache-read capture mechanism, replace the backtest driver's silent `except: pass` guards with loud failures, and stop the upstream verifier from rendering placeholder rows as green.

**Architecture:** Three localised refactors in `src/backtest/`. Capture mechanism is unified to a single inline implementation; the driver fails loudly when prerequisites are missing instead of skipping; the upstream verifier returns an explicit `None`/`skip` value (rendered as `skip`, never `pass`).

**Tech Stack:** Python 3.12, pytest, `backtest.cache.CachedDataStore`, `backtest.driver.Driver`, `backtest.audit.upstream_verifier`.

---

## 1. Goal + trust contract

### Goal (one paragraph)

Three lines in the backtest stack are quietly lying. (a) Two parallel "capture every cache read" mechanisms exist (`CachedDataStore._audit_*` vs `backtest.audit.AuditingStore`) — same shape, different surface, so it is never obvious which one a given call site actually exercises. (b) The driver wraps four prerequisite calls in `except RuntimeError: pass` blocks so unit tests can construct a `Driver` without a store; in a real run, those swallows turn missing reference-prices, missing audit drains, and missing broker price refreshes into ticks that proceed against stale or absent data. (c) `upstream_verifier._verify_filing` and `_verify_news` are placeholder bodies that hard-code `agreement_with_cache=True`, so the deep-dump SUMMARY can never report disagreement — the tripwire is permanently green-on-skip.

This plan ends the dual mechanism, raises on the silent guards, and teaches the verifier to admit when it skipped.

### Trust contract

**This plan trusts Plan 04 (lifecycle parity) has landed:** the backtest tick runs the live pipeline through a symmetric `install_plugins` path and `state["as_of"]` is always ISO-string at every callback. Plan 10 does not re-validate either invariant; if Plan 04 regresses, Plan 10's driver-guard tests will surface noise that should be filed back to Plan 04.

**Plans 11+ trust Plan 10 to leave behind:**
- Exactly one row-capture surface (named below in §2), with the other deleted from `src/` and `scripts/`.
- A driver that raises `RuntimeError` with a specific message when the store handle is missing in a real `run()` invocation — unit tests must opt in via an explicit `Driver(..., require_store=False)` flag or equivalent test seam.
- An `upstream_verifier` that returns one of three explicit states per row (`ok` / `disagree` / `skip`), and a SUMMARY renderer that never counts `skip` as `ok`.

Plan 11 (test consolidation) will write its assertions against the post-plan-10 honest outputs.

### Out of scope (deferred work)

- Per-window cache compartmentalisation — see `[[project_backtest_cache_compartmentalisation_deferred]]`. Do not propose folder splits unprompted.
- PIT-correctness / leak audit — see `[[project_backtest_pit_correctness_deferred]]`. The verifier honesty work in this plan is structural (no green-on-skip), not a leak hunt.
- A-077 (`_audit_capture_enabled` single-caller inline) — folded into §2 below; if the capture mechanism is collapsed inline, the helper disappears for free.
- A-079 (`test_end_to_end_smoke.py` mega-file split) — that is a Plan 11 concern.

---

## 2. Capture-mechanism decision

### The decision

**Surviving mechanism: the in-store `_audit_*` capture on `CachedDataStore`.**
**Deleted mechanism: `backtest.audit.auditing_store.AuditingStore`.**

### Why this direction

Both mechanisms emit the same `{domain: {ticker: [rows]}}` shape. The split exists because they were written for two different entry points: `_audit_*` for the driver's per-tick telemetry, `AuditingStore` for the one-off deep-dump CLI (`scripts/backtest_audit_tick.py`).

The in-store mechanism wins on three counts:

1. **Single object identity.** Every read inside `CachedDataStore` already lives on that class. Adding capture as a method on the same class means the capture sees *every* read by construction — no risk of a future `read_*` method being added without a corresponding decorator override.
2. **Already wired into the driver.** `driver.run()` already calls `_audit_enable_capture()` and `_audit_drain_reads()`. The deep-dump CLI (`scripts/backtest_audit_tick.py`) currently relies on `AuditingStore.__getattr__` falling through to the inner store — but it would work identically against a bare `CachedDataStore` with capture enabled, because the API surface is the same.
3. **No `__getattr__` magic.** `AuditingStore` uses dynamic delegation, which makes type-checking and IDE navigation poor; the in-store path is plain methods.

The audit memo `[[project_replay_backtest_manual_tool]]` does NOT cover `backtest_audit_tick.py` — only `replay_backtest.py`. `replay_backtest.py` does not touch the capture machinery (verified via grep in research), so this consolidation cannot break the manual replay tool. `backtest_audit_tick.py` is itself an audit harness that we own and migrate in this plan.

### Migration of the loser

- Delete `src/backtest/audit/auditing_store.py` and `tests/backtest/audit/test_auditing_store.py`.
- Rewrite `scripts/backtest_audit_tick.py` to construct a bare `CachedDataStore`, enable capture on it, run the tick, then drain via `_audit_drain_reads()`.
- Update `src/backtest/audit/__init__.py` docstring to remove the `AuditingStore` reference.
- Update `src/backtest/audit/deep_dump.py` if it imports `AuditingStore` for type hints only.
- Inline `_audit_capture_enabled` into its single caller (`_audit_record`) — this resolves A-077 as a free side effect.

---

## 3. Ordered changes

Tasks execute in this order. Each task is a self-contained commit (TDD; commit after green).

### Task 1: Inline `_audit_capture_enabled` (A-077)

**Files:**
- Modify: `src/backtest/cache/store.py:867-885`
- Test: existing `tests/backtest/audit/` covers behaviour; add no new test (pure refactor).

- [ ] **Step 1: Read existing behaviour**

Read `src/backtest/cache/store.py:858-902`. Confirm `_audit_capture_enabled` has exactly one caller (`_audit_record`).

- [ ] **Step 2: Run the existing audit tests to establish a green baseline**

Run: `.venv/bin/python -m pytest tests/backtest/ -k "audit" -v`
Expected: PASS (or skipped) — record the count.

- [ ] **Step 3: Inline the helper**

Replace this region in `src/backtest/cache/store.py`:

```python
    def _audit_capture_enabled(self) -> bool:
        """Return ``True`` iff per-tick read capture is currently on."""
        return getattr(self, "_audit_reads", None) is not None

    def _audit_record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        """Append ``rows`` into the per-tick capture if enabled.

        Parameters
        ----------
        domain:
            Domain key (e.g. ``"news"``, ``"price_history"``).
        ticker:
            Ticker symbol.
        rows:
            Model instances returned by the read method.
        """
        if not self._audit_capture_enabled():
            return
        self._audit_reads.setdefault(domain, {}).setdefault(ticker, []).extend(rows)
```

with:

```python
    def _audit_record(self, domain: str, ticker: str, rows: list[Any]) -> None:
        """Append ``rows`` into the per-tick capture buffer if enabled.

        Capture is enabled by ``_audit_enable_capture`` and drained by
        ``_audit_drain_reads``.  When ``self._audit_reads`` is absent
        (the live default) this is a no-op.

        Parameters
        ----------
        domain:
            Domain key (e.g. ``"news"``, ``"price_history"``).
        ticker:
            Ticker symbol.
        rows:
            Model instances returned by the read method.
        """
        # No buffer attached → capture is disabled; nothing to do.
        if getattr(self, "_audit_reads", None) is None:
            return
        self._audit_reads.setdefault(domain, {}).setdefault(ticker, []).extend(rows)
```

- [ ] **Step 4: Re-run audit tests**

Run: `.venv/bin/python -m pytest tests/backtest/ -k "audit" -v`
Expected: PASS — same count as Step 2.

- [ ] **Step 5: Commit**

```bash
git add src/backtest/cache/store.py
git commit -m "refactor(backtest): inline _audit_capture_enabled single caller"
```

---

### Task 2: Delete `AuditingStore` and migrate the audit CLI (A-043)

**Files:**
- Delete: `src/backtest/audit/auditing_store.py`
- Delete: `tests/backtest/audit/test_auditing_store.py`
- Modify: `scripts/backtest_audit_tick.py:32, 85-96, 122-123`
- Modify: `src/backtest/audit/__init__.py:8` (docstring)
- Modify: `src/backtest/audit/deep_dump.py` (only if it imports `AuditingStore`)

- [ ] **Step 1: Write the failing migration test for the CLI**

Add `tests/backtest/audit/test_backtest_audit_tick_uses_in_store_capture.py`:

```python
"""The deep-dump CLI must capture rows via CachedDataStore._audit_* —
not via a separate decorator.  Pinning the surface prevents the
two-mechanism split from reappearing.
"""
from __future__ import annotations

import importlib


def test_backtest_audit_tick_does_not_import_auditing_store():
    """Audit CLI must not reference the deleted AuditingStore class."""
    module = importlib.import_module("scripts.backtest_audit_tick")
    # The symbol must not exist on the module — neither as import nor reference.
    assert not hasattr(module, "AuditingStore"), (
        "scripts.backtest_audit_tick still references AuditingStore — "
        "use CachedDataStore._audit_enable_capture / _audit_drain_reads."
    )


def test_auditing_store_module_is_gone():
    """The redundant module must be deleted, not just unreferenced."""
    import pytest
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("backtest.audit.auditing_store")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/backtest/audit/test_backtest_audit_tick_uses_in_store_capture.py -v`
Expected: FAIL — `AuditingStore` still present.

- [ ] **Step 3: Rewrite `scripts/backtest_audit_tick.py`**

Replace the imports and the store-construction block. The exact edits:

Replace:
```python
from backtest.audit.auditing_store import AuditingStore
```
with: (delete the line)

Replace the store-construction region (lines around 85-96):
```python
    inner = CachedDataStore(cache_path)
    store = AuditingStore(inner=inner)

    # Register the auditing store as the active store for this process.
    # Providers call get_store() to read from it during the tick replay.
    set_store(store)  # type: ignore[arg-type]  # AuditingStore delegates all methods
```
with:
```python
    # Build a plain cache store and enable per-tick read capture on it.
    # Plan 10 collapsed the AuditingStore decorator into the store itself,
    # so a single API surface drives both the live driver and this CLI.
    store = CachedDataStore(cache_path)
    store._audit_enable_capture()

    # Register the capturing store as the active store for this process.
    # Providers call get_store() to read from it during the tick replay.
    set_store(store)
```

Replace the drain call:
```python
    captured = store.drain_captured()
```
with:
```python
    captured = store._audit_drain_reads()
```

- [ ] **Step 4: Delete the redundant module and its tests**

```bash
git rm src/backtest/audit/auditing_store.py
git rm tests/backtest/audit/test_auditing_store.py
```

- [ ] **Step 5: Scrub stale references**

Update `src/backtest/audit/__init__.py` — replace any sentence mentioning `AuditingStore` with: `"The cache store captures every read via its own ``_audit_*`` API; see ``backtest.cache.store.CachedDataStore``."`

Grep for orphan references and remove:

```bash
grep -rn "AuditingStore\|auditing_store\|drain_captured" src/ scripts/ tests/
```
Expected after fix: no matches.

- [ ] **Step 6: Run the migration tests + full backtest suite**

Run:
```bash
.venv/bin/python -m pytest tests/backtest/ -v
.venv/bin/python -m ruff check src/backtest/ scripts/backtest_audit_tick.py
```
Expected: PASS; ruff clean.

- [ ] **Step 7: Smoke-run the audit CLI against a real fixture run (if available)**

If a `runs/<window>/<run-id>/` directory exists locally:
```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_audit_tick \
    --window <window-key> --run-id <run-id> \
    --tick <iso-as-of> --phase close
```
Expected: writes `audit/<slug>.full.json` and `audit/<slug>.summary.json` without error. If no run dir exists locally, skip this step and rely on the unit tests.

- [ ] **Step 8: Commit**

```bash
git add -A src/backtest/audit/ scripts/backtest_audit_tick.py tests/backtest/audit/
git commit -m "refactor(backtest): collapse AuditingStore into CachedDataStore capture API"
```

---

### Task 3: Driver guard quartet — raise instead of silently skipping (A-044, A-045, A-078-driver-half)

**Files:**
- Modify: `src/backtest/driver.py:201-208` (capture-enable guard)
- Modify: `src/backtest/driver.py:290-319` (reference-prices guard)
- Modify: `src/backtest/driver.py:343-355` (audit-drain guard)
- Modify: `src/backtest/driver.py:597-601` (`(AttributeError, Exception)` tuple — A-045)
- Modify: `src/backtest/driver.py:687-694` (`_refresh_broker_prices` guard)
- Test: `tests/backtest/test_driver_guards_raise.py` (new)

#### Design — the `require_store` test seam

The four silent guards exist because unit tests construct `Driver` without wiring `backtest.providers._store_handle`. The fix is a single constructor flag, `require_store: bool = True`. When `True` (the default, used by `Runner` in production), all four sites raise immediately with a specific message naming the missing prerequisite. When `False` (opt-in for isolated unit tests), each site logs a `WARNING` on first miss and proceeds with the documented degenerate path.

This is the only way to satisfy "raise loudly in real runs" without forcing every existing unit test to construct a fake store. Inline notes record which sites are gated by the flag so a future reader can audit.

#### Steps

- [ ] **Step 1: Write the failing test — production driver must raise on missing store**

Create `tests/backtest/test_driver_guards_raise.py`:

```python
"""Driver guards must raise loudly when the store handle is missing in
production mode (``require_store=True`` — the default).  Plan 10 replaces
the previous ``except RuntimeError: pass`` quartet with explicit failure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backtest.driver import Driver
from backtest.schedule import Tick


class _NullBroker:
    """Minimal broker stub — has no behaviour the constructor needs."""

    async def get_portfolio(self):
        from broker.models import Portfolio
        return Portfolio(cash=0.0, holdings=[])

    def set_price(self, ticker: str, price: float) -> None:  # pragma: no cover
        pass


def test_driver_raises_when_store_missing_in_production_mode(tmp_path: Path):
    """Default Driver construction (require_store=True) must raise the
    moment a tick runs without a wired store handle — no silent skip."""
    # No call to set_store() — store handle is empty.
    driver = Driver(
        broker=_NullBroker(),
        run_dir=tmp_path,
        window_key="unit-test",
        run_id="unit-test-run",
        # require_store defaults to True
    )

    tick = Tick(as_of=datetime(2026, 1, 2, 13, 30, tzinfo=timezone.utc), phase="open")

    import asyncio
    with pytest.raises(RuntimeError, match="store handle not wired"):
        asyncio.run(driver.run({"tickers": ["AAPL"]}, [tick]))


def test_driver_does_not_raise_when_require_store_disabled(tmp_path: Path):
    """Opt-in escape hatch for isolated unit tests — must log a WARNING
    but not raise.  No tick will execute because the broker stub returns
    an empty portfolio and the pipeline build will short-circuit; we are
    only asserting the guards themselves do not raise."""
    driver = Driver(
        broker=_NullBroker(),
        run_dir=tmp_path,
        window_key="unit-test",
        run_id="unit-test-run",
        require_store=False,
    )

    # Driver construction alone must not raise — the capture-enable guard
    # is exercised in __init__.
    assert driver is not None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/backtest/test_driver_guards_raise.py -v`
Expected: FAIL — current driver swallows the `RuntimeError` from `get_store()`.

- [ ] **Step 3: Add the `require_store` flag to `Driver.__init__`**

In `src/backtest/driver.py`, extend the signature of `Driver.__init__` to accept `require_store: bool = True`. Store it on `self._require_store`. Replace the existing capture-enable block:

```python
        # Enable per-tick read capture on the shared cache store so the audit
        # telemetry layer can summarise what the analysts saw.
        try:
            from backtest.providers._store_handle import get_store
            get_store()._audit_enable_capture()
        except RuntimeError:
            # No store wired (unit tests) — telemetry will be empty.
            pass
```

with:

```python
        # Enable per-tick read capture on the shared cache store so the audit
        # telemetry layer can summarise what the analysts saw.
        #
        # Production runs (``require_store=True`` — the default) raise loudly
        # when the store is missing: silently skipping capture used to mean
        # the audit telemetry quietly recorded zero reads, masking real
        # pipeline regressions (see Plan 10 §3, A-044).
        from backtest.providers._store_handle import get_store
        try:
            get_store()._audit_enable_capture()
        except RuntimeError as exc:
            if self._require_store:
                raise RuntimeError(
                    "Driver: store handle not wired — call "
                    "backtest.providers._store_handle.set_store(...) before "
                    "constructing Driver, or pass require_store=False for "
                    "isolated unit tests."
                ) from exc
            logger.warning(
                "Driver constructed with require_store=False — audit "
                "telemetry will be empty for this run."
            )
```

- [ ] **Step 4: Replace the reference-prices guard**

Replace:
```python
            except RuntimeError:
                # Store handle not initialised (e.g. isolated unit tests that
                # construct Driver without a real cache) — leave reference_prices
                # unchanged so those paths do not break.
                pass
```

with:
```python
            except RuntimeError as exc:
                if self._require_store:
                    raise RuntimeError(
                        f"Driver: cannot refresh reference_prices for tick "
                        f"{tick.as_of.isoformat()} — store handle not wired."
                    ) from exc
                logger.warning(
                    "Driver tick %s: reference_prices not refreshed "
                    "(require_store=False).", tick.as_of.isoformat(),
                )
```

- [ ] **Step 5: Replace the audit-drain guard**

Replace:
```python
            try:
                _store       = _get_store()
                cache_reads  = _store._audit_drain_reads()
            except RuntimeError:
                # Store not wired in isolated unit tests — produce empty telemetry.
                cache_reads = {}
```

with:
```python
            try:
                _store       = _get_store()
                cache_reads  = _store._audit_drain_reads()
            except RuntimeError as exc:
                if self._require_store:
                    raise RuntimeError(
                        f"Driver: cannot drain audit reads for tick "
                        f"{tick.as_of.isoformat()} — store handle not wired."
                    ) from exc
                logger.warning(
                    "Driver tick %s: audit telemetry empty "
                    "(require_store=False).", tick.as_of.isoformat(),
                )
                cache_reads = {}
```

- [ ] **Step 6: Replace the broker-price-refresh guard**

In `_refresh_broker_prices` replace:
```python
        try:
            store = get_store()
        except RuntimeError:
            # Store not wired (e.g. in isolated unit tests) — skip silently.
            return
```

with:
```python
        try:
            store = get_store()
        except RuntimeError as exc:
            if self._require_store:
                raise RuntimeError(
                    f"Driver: cannot refresh broker prices for tick "
                    f"{tick.as_of.isoformat()} — store handle not wired."
                ) from exc
            logger.warning(
                "Driver tick %s: broker prices not refreshed "
                "(require_store=False).", tick.as_of.isoformat(),
            )
            return
```

- [ ] **Step 7: Fix A-045 — the redundant `(AttributeError, Exception)` tuple**

Replace `src/backtest/driver.py:599`:
```python
        except (AttributeError, Exception) as exc:
```
with:
```python
        except Exception as exc:
```

(`AttributeError` is a subclass of `Exception`; listing both is dead — keep the existing surrounding comment about `BaseException` not being caught.)

- [ ] **Step 8: Audit existing tests for callers that need `require_store=False`**

Run:
```bash
grep -rn "Driver(" tests/ | grep -v "test_driver_guards_raise"
```

For each constructor call that does not wire `set_store(...)`, add `require_store=False`. Common offenders will be in `tests/backtest/test_*driver*.py`. Run each touched test file to confirm green.

- [ ] **Step 9: Run the full backtest test suite**

```bash
.venv/bin/python -m pytest tests/backtest/ -v
```
Expected: PASS, including the two new guard tests.

- [ ] **Step 10: Commit**

```bash
git add src/backtest/driver.py tests/backtest/test_driver_guards_raise.py tests/backtest/
git commit -m "fix(backtest): raise loudly on missing store; add require_store opt-out for unit tests"
```

---

### Task 4: Upstream verifier honesty (A-078)

**Files:**
- Modify: `src/backtest/audit/upstream_verifier.py:159-217`
- Modify: any SUMMARY renderer that maps verifier output to `pass`/`fail` (locate via grep in Step 1)
- Test: `tests/backtest/audit/test_upstream_verifier_honest.py` (new)

#### Design — three explicit states

`_verify_filing` and `_verify_news` currently return `{"agreement_with_cache": True, ...}` even when the body has not actually performed the verification. The fix introduces a three-way tag:

```python
# verification_status: one of these three strings.
"ok"       # The verifier ran and the upstream matched the cache.
"disagree" # The verifier ran and the upstream contradicted the cache.
"skip"     # The verifier did not run (no accession, no URL, network disabled, etc.)
```

`agreement_with_cache` is removed from the return shape. Downstream readers map states to verdicts:

| `verification_status` | SUMMARY verdict |
|---|---|
| `ok`       | counted as `verified` |
| `disagree` | counted as `disagree` (red) |
| `skip`     | counted as `skipped` (neutral — never `verified`) |

The current placeholder bodies return `skip` (because they never actually hit the network). When real implementations land, they can return `ok` or `disagree`.

#### Steps

- [ ] **Step 1: Locate the SUMMARY consumer of verifier output**

Run:
```bash
grep -rn "agreement_with_cache\|_verify_filing\|_verify_news" src/ tests/
```

Note every caller. Expected: `src/backtest/audit/deep_dump.py` (constructs rows), plus SUMMARY render in the same file or in a sibling. Read the relevant function to confirm the current "all-true → all-pass" rendering.

- [ ] **Step 2: Write the failing test**

Create `tests/backtest/audit/test_upstream_verifier_honest.py`:

```python
"""Upstream verifier must distinguish "ran and agreed" from "did not run".
Plan 10 §4 — no green-on-skip rendering."""
from __future__ import annotations

from types import SimpleNamespace

from backtest.audit.upstream_verifier import _verify_filing, _verify_news


def test_verify_filing_returns_skip_when_no_accession():
    """A filing row with no accession_no cannot be verified — status must
    be 'skip', not 'ok'."""
    row = SimpleNamespace(accession_no=None, id=None)
    result = _verify_filing(row)
    assert result["verification_status"] == "skip"
    assert "agreement_with_cache" not in result, (
        "Boolean agreement field must be removed — replaced by tri-state "
        "verification_status."
    )


def test_verify_filing_placeholder_returns_skip_even_with_accession():
    """Until the real sec.gov fetcher is wired, the body must self-report
    as a skip — never green-on-placeholder."""
    row = SimpleNamespace(accession_no="0001234567-26-000001")
    result = _verify_filing(row)
    assert result["verification_status"] == "skip"


def test_verify_news_placeholder_returns_skip():
    """Same contract for news — placeholder body must self-report skip."""
    row = SimpleNamespace(url="https://example.com/article")
    result = _verify_news(row)
    assert result["verification_status"] == "skip"


def test_summary_renderer_counts_skip_separately_from_ok():
    """The SUMMARY must not collapse 'skip' into the 'verified' bucket."""
    # Locate the actual renderer in Step 1 and import it here.
    # Placeholder shape — adapt to the real function found in Step 1:
    from backtest.audit.deep_dump import summarise_verification_states
    counts = summarise_verification_states([
        {"verification_status": "ok"},
        {"verification_status": "ok"},
        {"verification_status": "skip"},
        {"verification_status": "disagree"},
    ])
    assert counts == {"ok": 2, "skip": 1, "disagree": 1}
```

If `summarise_verification_states` does not exist, replace the fourth test with one that exercises whatever function Step 1 identified as the renderer (or add the function as part of Step 3).

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/backtest/audit/test_upstream_verifier_honest.py -v`
Expected: FAIL.

- [ ] **Step 4: Rewrite `_verify_filing` and `_verify_news`**

Replace the body of `_verify_filing`:

```python
def _verify_filing(row: Any) -> dict[str, Any]:
    """Verify a filing row's ``filed_at`` against the SEC submissions API.

    Returns a tri-state ``verification_status`` instead of the old
    ``agreement_with_cache`` boolean (Plan 10 §4 — no green-on-skip).

    * ``"ok"``       — verifier ran and the upstream matched the cache.
    * ``"disagree"`` — verifier ran and the upstream contradicted the cache.
    * ``"skip"``     — verifier did not run (no accession, network
                       disabled, placeholder body).

    Parameters
    ----------
    row:
        A filing row object, expected to have an ``accession_no`` or
        ``id`` attribute.

    Returns
    -------
    dict
        ``{"source": str, "verification_status": str, ...}``.
    """
    accession = getattr(row, "accession_no", None) or getattr(row, "id", None)
    if not accession:
        # No identifier — cannot verify.  Skip, do not pretend to pass.
        return {
            "source":              "(no-verify)",
            "verification_status": "skip",
            "reason":              "missing accession_no/id",
        }

    # TODO Plan 10 follow-up: implement the sec.gov fetch.  Until then,
    # self-report as skip so the SUMMARY never renders an unrun verifier
    # as green.
    return {
        "source":              f"sec.gov/Archives/.../{accession}-index.json",
        "accepted_datetime":   None,
        "verification_status": "skip",
        "reason":              "verifier not yet implemented",
    }
```

Replace the body of `_verify_news` analogously:

```python
def _verify_news(row: Any) -> dict[str, Any]:
    """Verify a news article's ``published_at`` against Tiingo.

    Same tri-state contract as ``_verify_filing`` — see its docstring.

    Parameters
    ----------
    row:
        A news article row, expected to have a ``url`` attribute.

    Returns
    -------
    dict
        ``{"source": str, "verification_status": str, ...}``.
    """
    url = getattr(row, "url", "")
    if not url:
        return {
            "source":              "(no-verify)",
            "verification_status": "skip",
            "reason":              "missing url",
        }

    # TODO Plan 10 follow-up: implement the Tiingo fetch.  Until then,
    # self-report as skip — never green-on-placeholder.
    return {
        "source":              url,
        "published_date":      None,
        "verification_status": "skip",
        "reason":              "verifier not yet implemented",
    }
```

- [ ] **Step 5: Update the SUMMARY renderer**

In the renderer located in Step 1, replace any boolean check on `agreement_with_cache` with a three-way bucketing on `verification_status`. If the renderer lives in `src/backtest/audit/deep_dump.py`, add `summarise_verification_states`:

```python
def summarise_verification_states(rows: list[dict]) -> dict[str, int]:
    """Count rows by tri-state ``verification_status``.

    Counts are emitted into the SUMMARY so an operator can tell at a
    glance how many rows actually got verified versus skipped.  A pure
    counter — never collapses ``skip`` into ``ok``.

    Parameters
    ----------
    rows:
        Verifier outputs (each a dict with ``verification_status``).

    Returns
    -------
    dict[str, int]
        Mapping ``{"ok": n_ok, "skip": n_skip, "disagree": n_disagree}``;
        any missing key defaults to zero in callers.
    """
    counts: dict[str, int] = {"ok": 0, "skip": 0, "disagree": 0}
    for row in rows:
        status = row.get("verification_status", "skip")
        counts[status] = counts.get(status, 0) + 1
    return counts
```

Wire this into wherever the SUMMARY text is composed so the rendered output reads e.g. `verified: 2 / disagree: 1 / skipped: 47 (verifier not yet implemented)` rather than the previous `all verified: ✓`.

- [ ] **Step 6: Update callers reading `agreement_with_cache`**

Run:
```bash
grep -rn "agreement_with_cache" src/ tests/
```
Replace each read with the equivalent `verification_status == "ok"` check, or update the caller's contract.

- [ ] **Step 7: Run the verifier test plus full backtest suite**

```bash
.venv/bin/python -m pytest tests/backtest/ -v
.venv/bin/python -m ruff check src/backtest/audit/
```
Expected: PASS, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add src/backtest/audit/upstream_verifier.py src/backtest/audit/deep_dump.py tests/backtest/audit/test_upstream_verifier_honest.py
git commit -m "fix(backtest): tri-state verifier — never render skip as verified"
```

---

### Task 5: Seed-prices raise on missing bars (A-046)

**Files:**
- Modify: `src/backtest/runner.py:149-186`
- Test: `tests/backtest/test_seed_initial_prices.py` (extend existing or create new)

- [ ] **Step 1: Locate or create the test file**

```bash
ls tests/backtest/ | grep -i "seed\|runner"
```

If a `test_seed_initial_prices.py` exists, extend it. Otherwise create one.

- [ ] **Step 2: Write the failing test**

Add to (or create) `tests/backtest/test_seed_initial_prices.py`:

```python
"""``_seed_initial_prices`` must raise when a ticker has no bars in the
window — the previous 0.0 default let FakeBroker accept zero-priced BUYs,
silently corrupting the backtest portfolio (A-046)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backtest.runner import _seed_initial_prices


class _StubStore:
    """Deterministic store stub for the seed-prices test."""

    def __init__(self, bars_by_ticker: dict[str, list]):
        self._bars = bars_by_ticker

    def read_ohlcv(self, ticker: str, start, end):
        return self._bars.get(ticker, [])


class _Bar:
    def __init__(self, close: float):
        self.close = close


def test_seed_initial_prices_raises_on_missing_bars():
    """A watchlist ticker with zero bars in the window must raise — never
    silently default to 0.0."""
    store = _StubStore({"AAPL": [_Bar(150.0)], "GHOST": []})
    with pytest.raises(ValueError, match="GHOST"):
        _seed_initial_prices(
            store=store,
            tickers=["AAPL", "GHOST"],
            window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )


def test_seed_initial_prices_happy_path():
    """All tickers have bars → returns close prices, no raise."""
    store = _StubStore({
        "AAPL": [_Bar(150.0)],
        "MSFT": [_Bar(420.0)],
    })
    prices = _seed_initial_prices(
        store=store,
        tickers=["AAPL", "MSFT"],
        window_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        window_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
    )
    assert prices == {"AAPL": 150.0, "MSFT": 420.0}
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/backtest/test_seed_initial_prices.py -v`
Expected: FAIL — current implementation defaults to 0.0.

- [ ] **Step 4: Rewrite `_seed_initial_prices` to raise**

Replace the body in `src/backtest/runner.py`:

```python
    prices: dict[str, float] = {}

    for ticker in tickers:
        bars = store.read_ohlcv(ticker, window_start, window_end)
        prices[ticker] = float(bars[0].close) if bars else 0.0

    return prices
```

with:

```python
    prices:  dict[str, float] = {}
    missing: list[str]        = []

    for ticker in tickers:
        bars = store.read_ohlcv(ticker, window_start, window_end)
        if not bars:
            # Collect every missing ticker before raising so the operator
            # sees the full list in one error rather than fix-and-retry.
            missing.append(ticker)
            continue
        prices[ticker] = float(bars[0].close)

    if missing:
        raise ValueError(
            f"_seed_initial_prices: no OHLCV bars in window "
            f"[{window_start.isoformat()}, {window_end.isoformat()}] "
            f"for tickers: {sorted(missing)}.  Run the fetcher for "
            f"these symbols before invoking the backtest."
        )

    return prices
```

Update the function docstring's behavioural paragraph to match.

- [ ] **Step 5: Run the new test plus full backtest suite**

```bash
.venv/bin/python -m pytest tests/backtest/test_seed_initial_prices.py -v
.venv/bin/python -m pytest tests/backtest/ -v
```
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/backtest/runner.py tests/backtest/test_seed_initial_prices.py
git commit -m "fix(backtest): raise on missing seed-price bars instead of 0.0 default"
```

---

### Task 6: Cleanup — A-084 (`_git_sha7` vs `_git_sha_full`) and A-085 (`build_telemetry_record_from_logs` orphan)

**Files:**
- Modify: `src/backtest/runner.py:632-660` (consolidate sha helpers)
- Modify: `src/backtest/audit/telemetry.py:77-106` (delete or wire orphan)
- Modify: `tests/backtest/test_cache_hits_audit.py:37-39` (test caller)

- [ ] **Step 1: A-084 — consolidate the two sha helpers**

Read `src/backtest/runner.py:632-660`. Replace the two functions with a single `_git_sha(*, length: int | None = None)` returning the full sha by default and truncating when `length` is given. Update the two callers (`runner.py:374` and `runner.py:485`).

```python
def _git_sha(*, length: int | None = None) -> str:
    """Return the current git HEAD sha; truncate to ``length`` chars if set.

    Parameters
    ----------
    length:
        If provided, return only the first ``length`` characters.  ``None``
        returns the full 40-character sha.

    Returns
    -------
    str
        Git HEAD sha (full or truncated), or ``"unknown"`` if the repo
        lookup fails (e.g. running outside a git checkout).
    """
    import subprocess
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return sha[:length] if length is not None else sha
```

Update call sites:
- `runner.py:374` → `_git_sha(length=7)`
- `runner.py:485` → `_git_sha()`
- Any test patches → patch `_git_sha`.

Update `src/backtest/settings.py:160,170` comment references from `_git_sha7` to `_git_sha`.

- [ ] **Step 2: A-085 — decide on `build_telemetry_record_from_logs`**

Run:
```bash
grep -rn "build_telemetry_record_from_logs" src/ scripts/ tests/
```

Only caller: `tests/backtest/test_cache_hits_audit.py`. The driver does not invoke it. **Decision: delete the function and the test.** The driver-resident path (`build_telemetry_record` + log-based cache-hit drain in `driver.py:368`) is the live mechanism; this orphan was the predecessor.

Delete `build_telemetry_record_from_logs` from `src/backtest/audit/telemetry.py`. Delete the test in `tests/backtest/test_cache_hits_audit.py` that calls it (the rest of the file may have other unrelated tests — only remove the affected test function).

- [ ] **Step 3: Run the full backtest suite**

```bash
.venv/bin/python -m pytest tests/backtest/ -v
.venv/bin/python -m ruff check src/backtest/
```
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/backtest/runner.py src/backtest/settings.py src/backtest/audit/telemetry.py tests/backtest/test_cache_hits_audit.py
git commit -m "refactor(backtest): merge _git_sha helpers; drop unused build_telemetry_record_from_logs"
```

---

## 4. Test strategy

### Coverage matrix

| Finding | Test (new or extended) | Asserts |
|---|---|---|
| A-043 | `tests/backtest/audit/test_backtest_audit_tick_uses_in_store_capture.py` | `AuditingStore` symbol gone; module unimportable |
| A-044 (capture-enable) | `tests/backtest/test_driver_guards_raise.py::test_driver_raises_when_store_missing_in_production_mode` | `RuntimeError` raised on construction |
| A-044 (reference-prices, audit-drain, broker-refresh) | same file, integration-style (drive a tick with no store) | each guard raises with its own specific message |
| A-044 (test seam) | same file, `test_driver_does_not_raise_when_require_store_disabled` | flag suppresses raise; logs warning |
| A-045 | covered by ruff + driver tests staying green after tuple-flatten | no behavioural test |
| A-046 | `tests/backtest/test_seed_initial_prices.py` (happy + missing paths) | raises with ticker list on missing; returns prices on full window |
| A-077 | covered by Task 1 inline refactor; existing audit tests stay green | no new test |
| A-078 | `tests/backtest/audit/test_upstream_verifier_honest.py` | placeholder bodies return `verification_status == "skip"`; summary counts `skip` separately from `ok` |
| A-084 | full backtest suite stays green after helper merge | no new test |
| A-085 | full backtest suite stays green after orphan deletion | no new test |

### Two anchor tests demanded by the brief

1. **Driver tick fails → run aborts loudly** — `test_driver_guards_raise.py::test_driver_raises_when_store_missing_in_production_mode` is the canonical case. The existing `_run_one_tick` failure-ratio mechanism (`driver.py:323-336`) already aborts when the per-tick exception ratio exceeds threshold; this test exercises the pre-tick path (constructor raises before any tick runs) which is even stricter.

2. **Verifier skip never renders as pass** — `test_upstream_verifier_honest.py::test_summary_renderer_counts_skip_separately_from_ok` and the per-function `_returns_skip_*` tests. The fixture is each verifier function's own placeholder body.

---

## 5. Risks / silent-regression checklist

Before each commit, the implementer must verify each item.

| Risk | Check |
|---|---|
| **`scripts/replay_backtest.py` broken.** This is the user's manually-driven replay tool (see `[[project_replay_backtest_manual_tool]]`); we must not break it. | `grep -n "AuditingStore\|drain_captured\|_audit_" scripts/replay_backtest.py` — expected empty. If `replay_backtest.py` ever did use `AuditingStore`, surface to the user before deleting. |
| **`scripts/backtest_audit_tick.py` regression.** The CLI is migrated, not deleted; a smoke run against a real fixture (Task 2 Step 7) is the integration check. | If no local `runs/` directory exists, ask the user to spot-check the CLI manually after merge. |
| **`scripts/debug_cache_audit.py` references.** Tertiary script. | `grep -n "AuditingStore\|drain_captured" scripts/debug_cache_audit.py` — expected empty. |
| **Unit tests that construct `Driver(...)` directly.** They will now raise unless `require_store=False` is passed. | Task 3 Step 8 audits and updates them in the same commit. |
| **Downstream consumers of `agreement_with_cache`.** Any code reading the boolean will break when the key disappears. | Task 4 Step 6 greps + migrates each. |
| **A historical artefact tree that contains old-format SUMMARY rows.** If the team has saved pre-Plan-10 audit dumps, the renderer change may make them inconsistent with new ones. | Documented and accepted — the new format strictly improves honesty; no migration of old artefacts is in scope. |
| **`_git_sha7` callers outside this audit's grep.** Possible tests patching the symbol by string. | Step 1 of Task 6 greps before deletion; any caller gets migrated. |
| **PIT/leak audit overreach.** Tempting to expand Task 4 into "now wire the real SEC fetcher". Do not. | Honour `[[project_backtest_pit_correctness_deferred]]` — Plan 10 only fixes the green-on-skip rendering bug. Real verifier bodies are a future spec. |
| **Per-window cache compartmentalisation drift.** Resist proposing folder splits while editing `scripts/backtest_audit_tick.py`. | Honour `[[project_backtest_cache_compartmentalisation_deferred]]`. |
| **British English drift in new code and docstrings.** | British spellings in all comments/docstrings touched. |

---

## 6. Definition of done

Plan 10 is complete when every item below is true:

- [ ] `backtest.audit.auditing_store` module deleted; no import of `AuditingStore` anywhere under `src/`, `scripts/`, or `tests/` (grep verifies).
- [ ] `scripts/backtest_audit_tick.py` uses `CachedDataStore._audit_enable_capture` / `_audit_drain_reads`; smoke run produces a deep-dump pair without error on at least one local run dir, or this is explicitly flagged for user smoke-test.
- [ ] `CachedDataStore._audit_capture_enabled` no longer exists (inlined into `_audit_record`).
- [ ] `Driver.__init__` accepts `require_store: bool = True`; with the default, all four previous silent-guard sites raise `RuntimeError` with site-specific messages when the store handle is unwired.
- [ ] Driver line 599 reads `except Exception as exc:` (the `(AttributeError, Exception)` tuple is gone).
- [ ] `_seed_initial_prices` raises `ValueError` listing every missing ticker; 0.0 fallback is gone.
- [ ] `_verify_filing` and `_verify_news` return `verification_status` in `{"ok", "skip", "disagree"}`; `agreement_with_cache` key is gone from the return shape.
- [ ] SUMMARY renderer counts `skip` rows separately from `ok` rows; no path can render `skip` as `verified`.
- [ ] `_git_sha7` and `_git_sha_full` consolidated into a single `_git_sha(*, length=None)`; both call-sites migrated.
- [ ] `build_telemetry_record_from_logs` deleted along with its single test caller.
- [ ] `.venv/bin/python -m pytest tests/backtest/ -v` is green.
- [ ] `.venv/bin/python -m ruff check src/backtest/ scripts/backtest_audit_tick.py` is clean.
- [ ] Six commits in the order: inline helper → delete AuditingStore → driver guards → verifier honesty → seed-prices raise → sha + orphan cleanup.

---

## Self-review notes

- **Spec coverage:** A-043 (§2 + Task 2), A-044 (Task 3), A-045 (Task 3 Step 7), A-046 (Task 5), A-077 (Task 1), A-078 (Task 4), A-084 (Task 6 Step 1), A-085 (Task 6 Step 2). All eight findings have an explicit task.
- **No placeholders:** Every step has either exact code, an exact command with expected outcome, or a grep/locate instruction the implementer can run mechanically. The only "locate then adapt" step is Task 4 Step 1, which is unavoidable (the SUMMARY renderer's exact name and location is implementation-detail that should not be guessed) — Step 1 produces a concrete file path the rest of the task builds on.
- **Type consistency:** `verification_status` string contract is used identically in Task 4 Steps 4, 5, and the test. `require_store` flag is consistent across Driver constructor and all four guard sites.
