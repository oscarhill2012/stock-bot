# Plan 01 — Safe deletions + doc-only intent edits

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the unambiguously dead symbols, files, and ORM table entries that later plans need cleared out of the way, and apply the three intent-doc edits resolved by gates §8.1, §8.5, §8.6.

**Architecture:** Pure deletes plus one tiny inlining refactor (A-094) and three prose edits to `intent.md`. No behavioural change is intended on any happy path; any test that fails after a deletion is signalling that the "dead" symbol was load-bearing and should be investigated, not patched.

**Trust contract:**
- **Trusts:** nothing — this plan is first in the sequence.
- **Trusted by later plans for:**
  - Plan 04 (A-011, `_STOCKBOT_TABLES` rebase onto `Base.metadata.tables.keys()`) trusts that `BufferEntryRow` (and therefore the `buffer_entries` tablename) is gone before it rebases.
  - Plans that touch `src/agents/memory/` trust `MemoryProjection` no longer exists.
  - Plans that touch `src/agents/analysts/report_cache.py` or `cache_callbacks.py` trust `log_cache_hit_to_state` is gone.
  - Plans that touch `src/agents/memory/writer.py` trust `_has_real_smart_money` has been inlined.
  - Plans editing intent prose trust §7.1 / §2.4 / §2.5 already reflect the §8 gate resolutions, so they need not re-litigate them.

---

## Scope cross-check against `FINDINGS.md`

The user-supplied scope list was: §8.1, §8.5, §8.6 intent edits; A-012, A-021 housekeeping, A-028, A-030, A-042, A-056, A-090, A-094, A-095, A-097 micro-deletions.

| ID | In scope? | Why / why not |
|---|---|---|
| §8.1 doc edit | **Yes** | Update intent §7.1 + §2.5 prose to reflect "smart_money shelved". Pure doc. |
| §8.5 doc edit | **No code edit; record only** | §8.5 says **keep** `src/lifecycle/scheduler.py` as-is. No code or doc change required — this plan records the explicit "no action" so later plans don't re-open the question. |
| §8.6 doc edit | **Yes** | Update intent §2.4 clamp-order text. Pure doc. |
| A-012 (`scripts/trace_tick.py`) | **Yes** | §8.4 resolution: delete outright. Pure delete. |
| A-021 housekeeping | **Yes** | §8.1 resolution is doc-only (keep code dormant). The "housekeeping" is the §7.1 prose edit, already covered above. **No source/tests touched** — confirms A-022/A-033 stay deferred. |
| A-028 (empty `src/agents/attribution/`) | **Partial** | The directory does **not exist** on disk (verified). Only the two **stale docstring refs** in `src/agents/strategist/decision_writer.py:60` and `src/agents/contract/evidence_writer.py:68` need correcting. Pure comment edits. |
| A-030 (`BufferEntryRow` shell) | **Yes** | §8.3 resolution: delete CRUD + ORM + test + the `_STOCKBOT_TABLES` entry. Pure delete. |
| A-042 (`MemoryProjection`) | **Yes** | §8.3 says "same fate, same pass". Pure delete. |
| A-056 (clamp order) | **Yes (doc only)** | §8.6 — source wins; intent §2.4 wording change. Zero code change (already covered by §8.6 doc edit row above). |
| A-090 (Cloud Scheduler shells) | **No** | §8.5 — keep as-is. Excluded — included in this table only to record the explicit decision. |
| A-094 (`_has_real_smart_money`) | **Yes (small refactor)** | Single-caller helper in `src/agents/memory/writer.py`. Inlining is mechanical; behaviour preserved. Border-line "pure deletion" — flagged in the silent-regression checklist. |
| A-095 (`log_cache_hit_to_state`) | **Yes** | Documented no-op. Delete the function and the one call site in `cache_callbacks.py`. |
| A-097 micro-deletions in scope | **Yes (subset)** | Restrict to the three unambiguous wins matching the prompt's "empty `__init__`s, dead aliases, stale comments" gloss: empty `src/deploy/` directory (F-ops-002); empty `src/baselines/__init__.py` (F-ops-013); zero-caller `emit_analyst_totals` + `emit_analyst_header` aliases in `src/observability/terminal_log.py` (F-ops-003). |
| A-097 items **excluded** | **No** | "Band 4" comment residue (F-backtest-015 + strategist/schema.py:103) — the surrounding comments are load-bearing and the planning prompt is unclear on whether comment-only edits qualify. Defer to a later doc-cleanup pass. Likewise duplicate session-service tests (F-orch-008), `get_handles` (has callers + tests — not dead), `_git_sha7`/`_git_sha_full` (A-084, not in this plan's brief). |

---

## File map

**Source files modified:**
- `src/orchestrator/persistence.py` — delete `BufferEntryRow` + `save_buffer_entry` + `load_recent_buffer` (lines 27-79).
- `src/lifecycle/initialise.py` — remove `"buffer_entries"` from `_STOCKBOT_TABLES` tuple (line 21).
- `src/lifecycle/hard_reset.py` — remove `"buffer_entries"` from `_STOCKBOT_TABLES` tuple (line 17).
- `src/agents/memory/schema.py` — delete `MemoryProjection` class (lines 22-38) and the now-unused `Counter` import.
- `src/agents/memory/writer.py` — inline `_has_real_smart_money` at its sole caller (line 142); delete the helper and update the comment at line 111 that references it.
- `src/agents/analysts/report_cache.py` — delete `log_cache_hit_to_state` (lines 547+).
- `src/agents/analysts/cache_callbacks.py` — drop the call site (lines 216-222) and the import (line 80).
- `src/agents/strategist/decision_writer.py:60` — correct stale `attribution/writer.py` docstring reference.
- `src/agents/contract/evidence_writer.py:68` — correct stale `attribution/writer.py` docstring reference.
- `src/observability/terminal_log.py` — delete `emit_analyst_totals` (line 663+) and `emit_analyst_header` (line 720+).

**Source files deleted:**
- `scripts/trace_tick.py` — outright delete (§8.4).
- `src/deploy/` — empty directory, `rmdir`.
- `src/baselines/__init__.py` — empty file, `git rm`.

**Test files deleted:**
- `tests/unit/test_buffer_persistence.py` — entire file (§8.3).
- `tests/unit/test_memory_schema.py` — covers both `BufferEntry` (kept) and `MemoryProjection` (deleted); rewrite to drop only the two `MemoryProjection` tests.

**Test files modified:**
- `tests/unit/test_init_db_script.py` — remove `"buffer_entries"` from the `EXPECTED_TABLES` set.

**Docs modified:**
- `docs/audits/2026-05-26-codebase-audit/intent.md` — §7.1 (smart_money shelved wording), §2.5 (attribution dir no longer exists), §2.4 (clamp order matches source).

---

## Ordered changes

Tasks are independent unless noted. Land each in its own commit so a regression bisects cleanly.

### Task 1: Delete `BufferEntryRow` + CRUD helpers and the `buffer_entries` table entry (A-030)

**Files:**
- Modify: `src/orchestrator/persistence.py:27-79`
- Modify: `src/lifecycle/initialise.py:21`
- Modify: `src/lifecycle/hard_reset.py:17`
- Modify: `tests/unit/test_init_db_script.py:10`
- Delete: `tests/unit/test_buffer_persistence.py`

- [ ] **Step 1: Delete the test file that pins the dead shell.**

Run:
```bash
git rm tests/unit/test_buffer_persistence.py
```

- [ ] **Step 2: Delete `BufferEntryRow`, `save_buffer_entry`, `load_recent_buffer`.**

In `src/orchestrator/persistence.py`, remove lines 27-79 inclusive — the `BufferEntryRow` class and both CRUD helpers. The `import json` at line 4 stays (still used by `save_analyst_evidence` etc.).

- [ ] **Step 3: Remove `"buffer_entries"` from `_STOCKBOT_TABLES`.**

Edit `src/lifecycle/initialise.py:21`:

```python
_STOCKBOT_TABLES = ("trade_log", "portfolio_snapshots")
```

Edit `src/lifecycle/hard_reset.py:17` identically.

- [ ] **Step 4: Update `EXPECTED_TABLES` in the init-db test.**

Edit `tests/unit/test_init_db_script.py:10`:

```python
EXPECTED_TABLES = {"trade_log", "portfolio_snapshots"}
```

This change is **intentionally temporary** — Plan 04 will rebase `_STOCKBOT_TABLES` onto `Base.metadata.tables.keys()` and rewrite this test entirely.

- [ ] **Step 5: Run the affected test suites.**

```bash
.venv/bin/python -m pytest tests/unit/test_init_db_script.py tests/unit/ -k "persistence or memory_writer or init_db or lifecycle" -v
```

Expected: green. If any test still imports `BufferEntryRow` / `save_buffer_entry` / `load_recent_buffer`, the deletion was load-bearing — stop and investigate; do not paper over with re-exports.

- [ ] **Step 6: Commit.**

```bash
git add src/orchestrator/persistence.py src/lifecycle/initialise.py src/lifecycle/hard_reset.py tests/unit/test_init_db_script.py tests/unit/test_buffer_persistence.py
git commit -m "$(cat <<'EOF'
chore(audit): delete unwired BufferEntryRow shell (§8.3, A-030)

Spec C buffer persistence was never wired. Delete the ORM class,
both CRUD helpers, the cementing test, and the buffer_entries entry
in _STOCKBOT_TABLES. Plan 04 will rebase _STOCKBOT_TABLES onto
Base.metadata.tables.keys() and rewrite the init-db test.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Delete `MemoryProjection` (A-042)

**Files:**
- Modify: `src/agents/memory/schema.py`
- Modify: `tests/unit/test_memory_schema.py`

- [ ] **Step 1: Delete the class and its `Counter` import.**

In `src/agents/memory/schema.py`, remove the `from collections import Counter` import (line 4) and the entire `MemoryProjection` class (lines 22-38). After the edit the file contains only the `BufferEntry` model.

- [ ] **Step 2: Drop the `MemoryProjection` tests; keep `BufferEntry` tests.**

In `tests/unit/test_memory_schema.py`, remove the import of `MemoryProjection`, remove `test_memory_projection_recent_limit` and `test_memory_projection_tag_frequency`. Keep `test_buffer_entry_rejects_long_summary` and `test_buffer_entry_accepts_max_summary`. The `_entry` helper stays (used by the surviving tests after the next plan touches them — if no longer referenced, delete it).

- [ ] **Step 3: Run the memory tests.**

```bash
.venv/bin/python -m pytest tests/unit/test_memory_schema.py tests/unit/ -k memory -v
```

Expected: green.

- [ ] **Step 4: Commit.**

```bash
git add src/agents/memory/schema.py tests/unit/test_memory_schema.py
git commit -m "$(cat <<'EOF'
chore(audit): delete unused MemoryProjection class (§8.3, A-042)

Spec C scaffolding alongside BufferEntryRow; never imported outside
its own test. Re-add when Spec C wires buffer compression.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Delete `scripts/trace_tick.py` (A-012, §8.4)

**Files:**
- Delete: `scripts/trace_tick.py`

- [ ] **Step 1: Verify nothing imports it.**

Run:
```bash
grep -rn "trace_tick" src/ tests/ scripts/ docs/ 2>/dev/null
```

Expected: only matches in `scripts/trace_tick.py` itself (which is being deleted) and possibly historical docs. If `src/` or `tests/` reference it, stop and surface to the user — the §8.4 resolution assumed no live consumers.

- [ ] **Step 2: Delete the file.**

```bash
git rm scripts/trace_tick.py
```

- [ ] **Step 3: Run the full test suite (cheap sanity).**

```bash
.venv/bin/python -m pytest tests/ -x --ignore=tests/integration -q
```

Expected: green.

- [ ] **Step 4: Commit.**

```bash
git add -u scripts/trace_tick.py
git commit -m "$(cat <<'EOF'
chore(audit): delete scripts/trace_tick.py (§8.4, A-012)

Surface-trace role covered by graphify-out, decision_logger, and
TraceWriter. The bare temp:_trace handle install was broken under
DatabaseSessionService anyway.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: Inline `_has_real_smart_money` at its sole caller (A-094)

**Files:**
- Modify: `src/agents/memory/writer.py`

- [ ] **Step 1: Inline the helper at the call site.**

In `src/agents/memory/writer.py`, the helper at lines 21-56 has one caller at line 142 (`smart_money_seen=_has_real_smart_money(state)`). Replace that call with a local generator expression that preserves dict-or-pydantic robustness:

```python
            smart_money_seen=any(
                not (
                    (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)) is None
                    or (
                        (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)).get("is_no_data")
                        if isinstance((ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)), dict)
                        else getattr(
                            (ev.get("verdict") if isinstance(ev, dict) else getattr(ev, "verdict", None)),
                            "is_no_data",
                            False,
                        )
                    )
                )
                for ev in state.get("smart_money_evidence", []) or []
            ),
```

That inline expression is intentionally ugly. If a reviewer objects, the cleaner option is to lift the verdict-extraction into one local variable per loop iteration — but that brings us back to a helper. Prefer the inlined version since the helper has only one caller and removing it is the explicit aim of the finding.

- [ ] **Step 2: Delete `_has_real_smart_money` (lines 21-56) and update the line-111 comment that references it.**

Remove the function block. In the comment block at lines 102-111 inside `_run_async_impl`, drop the trailing sentence `"This mirrors the permissive-read pattern already used for ``final_orders`` in ``executor/agent.py`` (model_validate-or-passthrough) and for ``smart_money_evidence`` in ``_has_real_smart_money`` above."` and replace it with `"This mirrors the permissive-read pattern already used for ``final_orders`` in ``executor/agent.py``."` so the reference to the deleted helper is gone.

- [ ] **Step 3: Run the memory-writer tests.**

```bash
.venv/bin/python -m pytest tests/ -k "memory_writer or has_real_smart_money or writer_smart_money" -v
```

Expected: green. Existing tests that exercise the `smart_money_seen` flag MUST still pass with the inlined logic; that is the regression-catch for this task.

- [ ] **Step 4: Commit.**

```bash
git add src/agents/memory/writer.py
git commit -m "$(cat <<'EOF'
refactor(audit): inline _has_real_smart_money at its single caller (A-094)

Single-caller helper folded into MemoryWriter._run_async_impl. No
behavioural change — same robustness to dict-vs-Pydantic evidence rows.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Delete `log_cache_hit_to_state` (A-095)

**Files:**
- Modify: `src/agents/analysts/report_cache.py`
- Modify: `src/agents/analysts/cache_callbacks.py`

- [ ] **Step 1: Drop the call site.**

In `src/agents/analysts/cache_callbacks.py`, remove lines 216-222 (the call to `log_cache_hit_to_state(...)`). Remove `log_cache_hit_to_state` from the import at line 80 — leave only `read_cache, write_cache`.

- [ ] **Step 2: Delete the function.**

In `src/agents/analysts/report_cache.py`, remove the entire `log_cache_hit_to_state` function (starts at line 547). The function's docstring already certifies it is a no-op since S3, so its removal is behaviour-neutral.

- [ ] **Step 3: Run the cache and analyst tests.**

```bash
.venv/bin/python -m pytest tests/ -k "report_cache or cache_callbacks or cache_hit" -v
```

Expected: green. If any test imports `log_cache_hit_to_state` directly, the deletion was load-bearing — stop and investigate.

- [ ] **Step 4: Commit.**

```bash
git add src/agents/analysts/report_cache.py src/agents/analysts/cache_callbacks.py
git commit -m "$(cat <<'EOF'
chore(audit): delete log_cache_hit_to_state no-op (A-095)

Function has been a documented no-op since the audit now drains
report_cache_hit from obs/logs/. Structured-log emission in
cache_callbacks remains the single source of truth.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: Delete the zero-caller `emit_analyst_totals` and `emit_analyst_header` (A-097 subset, F-ops-003)

**Files:**
- Modify: `src/observability/terminal_log.py`

- [ ] **Step 1: Confirm zero callers one last time.**

```bash
grep -rn "emit_analyst_totals\|emit_analyst_header" src/ tests/ scripts/ 2>/dev/null
```

Expected: only the two `def`s in `src/observability/terminal_log.py`. If anything else, stop and surface.

- [ ] **Step 2: Delete both functions.**

In `src/observability/terminal_log.py`, remove `emit_analyst_totals` (starts line 663) and `emit_analyst_header` (starts line 720). Remove any module-level helpers that become unused as a consequence (re-check imports after the delete).

- [ ] **Step 3: Run observability tests.**

```bash
.venv/bin/python -m pytest tests/unit/observability/ -v
```

Expected: green.

- [ ] **Step 4: Commit.**

```bash
git add src/observability/terminal_log.py
git commit -m "$(cat <<'EOF'
chore(audit): delete dead emit_analyst_totals/_header aliases (A-097)

Zero callers across src/, tests/, scripts/. Per F-ops-003.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: Delete empty `src/deploy/` and empty `src/baselines/__init__.py` (A-097 subset, F-ops-002 + F-ops-013)

**Files:**
- Delete: `src/deploy/` (empty directory)
- Delete: `src/baselines/__init__.py` (zero-byte file)

- [ ] **Step 1: Verify both are truly empty.**

```bash
find src/deploy -type f 2>/dev/null
ls -la src/baselines/__init__.py
```

Expected: `src/deploy/` has no files; `__init__.py` is 0 bytes.

- [ ] **Step 2: Delete the empty `__init__.py`.**

```bash
git rm src/baselines/__init__.py
```

Note: Python implicit-namespace packages (PEP 420) mean `src/baselines/` continues to import as a package without an `__init__.py`. This is verified by the surviving `equity_curve.py` and `spy.py` modules being import-tested by the broader suite.

- [ ] **Step 3: Remove the empty directory.**

```bash
rmdir src/deploy
```

`git rm` cannot remove an empty untracked directory; `rmdir` is sufficient because there are no tracked files inside it.

- [ ] **Step 4: Run a broad smoke.**

```bash
.venv/bin/python -m pytest tests/ -x -q
```

Expected: green. Catches accidental `from baselines import something` that relied on a namespace marker.

- [ ] **Step 5: Commit.**

```bash
git add -u src/baselines/__init__.py
git commit -m "$(cat <<'EOF'
chore(audit): delete empty src/deploy/ and src/baselines/__init__.py (A-097)

Empty deploy/ dir (F-ops-002) and zero-byte baselines/__init__.py
(F-ops-013). Implicit-namespace packages handle the remaining baselines
modules without an explicit marker.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: Correct stale `attribution/writer.py` docstring refs (A-028)

**Files:**
- Modify: `src/agents/strategist/decision_writer.py:60`
- Modify: `src/agents/contract/evidence_writer.py:68`

- [ ] **Step 1: Update both comments.**

In `src/agents/strategist/decision_writer.py`, line 60 currently reads:

```python
        # and mirror the style used in attribution/writer.py.
```

Change to:

```python
        # and keep the orchestrator.persistence dependency lazy so this
        # module imports without a configured ORM session.
```

In `src/agents/contract/evidence_writer.py`, lines 68-70 currently read:

```python
        # Lazy import mirrors the style used in attribution/writer.py and
        # keeps this module importable in environments that stub out
        # orchestrator.persistence.
```

Change to:

```python
        # Lazy import keeps this module importable in environments that
        # stub out orchestrator.persistence.
```

- [ ] **Step 2: Confirm no other stale `attribution/` mentions remain in source.**

```bash
grep -rn "attribution/writer\|attribution\.writer\|src/agents/attribution" src/ 2>/dev/null
```

Expected: empty.

- [ ] **Step 3: Run a broad smoke (comment-only edits are unlikely to fail tests but verify anyway).**

```bash
.venv/bin/python -m pytest tests/unit/ -x -q
```

Expected: green.

- [ ] **Step 4: Commit.**

```bash
git add src/agents/strategist/decision_writer.py src/agents/contract/evidence_writer.py
git commit -m "$(cat <<'EOF'
docs(audit): drop stale attribution/writer.py docstring refs (A-028)

src/agents/attribution/ no longer exists on disk. Replace the
references with a description of what the lazy import actually
achieves.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: Apply intent §7.1, §2.5, §2.4 doc edits (§8.1, §8.5 record, §8.6)

**Files:**
- Modify: `docs/audits/2026-05-26-codebase-audit/intent.md`

- [ ] **Step 1: Rewrite §7.1 to reflect "smart_money is shelved" (§8.1).**

At `docs/audits/2026-05-26-codebase-audit/intent.md:763-769`, the current §7.1 says smart_money "runs every tick". Replace with:

```markdown
### 7.1 Smart-money analyst (resolves §6.1 — see §8.1)
**Status:** **Shelved.** `pipeline.py:88` has `_build_smart_money_analyst(...)`
commented out, deliberately, pending PIT-correct providers for
`notable_holders` and `politician_trades`. The analyst module
(`src/agents/analysts/smart_money/`), its fetcher, and its test suite are
kept as dormant scaffolding for reactivation; do not delete them.
**Audit implication:** Defensive consumer code that handles
`smart_money_evidence` **absence** is correct, not dead, for the shelved
state.
```

- [ ] **Step 2: Update §2.5 to drop "and empty `attribution/`".**

At line 194:

```markdown
### 2.5 agents-misc — `src/agents/{memory,snapshot,isolated_failure.py,llm_retry.py}`
```

(The "(and empty `attribution/`)" parenthetical is removed because the directory no longer exists on disk after this plan.)

- [ ] **Step 3: Replace the §2.4 clamp-order invariant text (§8.6).**

At lines 182-185, replace the "Clamps applied in order: ... no-short rule" bullet with the authoritative source-derived order:

```markdown
- Clamps applied in order, matching `src/agents/risk_gate/`:
  1. `apply_buy_delta_clamp` (defence-in-depth, in `agent.py` before
     `apply_constraints`)
  2. `_clamp_negatives` (no-short — runs **first** inside
     `apply_constraints` so subsequent clamps operate on non-negative
     weights)
  3. `_clamp_max_position` (concentration cap)
  4. `_clamp_cash_floor`
  5. `_clamp_max_delta` (per-ticker delta)
  6. `_clamp_max_turnover` (total turnover)
```

- [ ] **Step 4: Append a §8.5 "no-op confirmation" note to the plan file (not the intent doc).**

No edit to `intent.md` for §8.5 — Cloud Scheduler stays as planned. Confirm the §8.5 row in the scope table above is sufficient documentation of the explicit decision.

- [ ] **Step 5: Visual diff review of `intent.md`.**

```bash
git diff docs/audits/2026-05-26-codebase-audit/intent.md
```

Confirm only the three sections above changed.

- [ ] **Step 6: Commit.**

```bash
git add docs/audits/2026-05-26-codebase-audit/intent.md
git commit -m "$(cat <<'EOF'
docs(audit): apply §8.1/§8.6 intent edits + drop attribution from §2.5

§7.1 — smart_money is shelved, not running every tick (§8.1, A-021).
§2.4 — clamp order matches source: no-short runs first (§8.6, A-056).
§2.5 — drop empty attribution/ reference (A-028).

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
EOF
)"
```

---

## Test strategy

**Tests deleted alongside code:**
- `tests/unit/test_buffer_persistence.py` — pinned the dead `BufferEntryRow` CRUD; nothing else to port.
- The two `MemoryProjection` tests in `tests/unit/test_memory_schema.py` — pinned the dead projection class.

**Tests modified to track the smaller `_STOCKBOT_TABLES` set:**
- `tests/unit/test_init_db_script.py` — `EXPECTED_TABLES` shrunk from 3 → 2. This test is **slated for full rewrite in Plan 04** when the tuple is derived from `Base.metadata.tables.keys()`; the edit here is the minimum to keep CI green in the interim.

**New tests required:** None. Every deletion in this plan removes a symbol that either (a) has no callers (`MemoryProjection`, `emit_analyst_totals/_header`, `log_cache_hit_to_state`, `BufferEntryRow` + CRUD) or (b) is a no-op the docstring already certifies (`log_cache_hit_to_state`). The one refactor (A-094 inlining of `_has_real_smart_money`) is covered by the existing memory-writer tests that exercise `smart_money_seen`; if those pass after the inlining, the refactor is correct.

**Regression-catch invariant for the whole plan:**

```bash
.venv/bin/python -m pytest tests/ -q
```

must finish green after every commit. A red bar at any task means the "dead" symbol was load-bearing — stop, investigate, do **not** restore the symbol; instead, surface to the user so the audit can be re-opened on that ID.

---

## Risks / silent-regression checklist

For each deletion, the one assertion that would catch it being load-bearing:

| Deletion | Assertion that fires if the delete was wrong |
|---|---|
| `BufferEntryRow` + CRUD | Any test that imports `BufferEntryRow` or calls `save_buffer_entry`/`load_recent_buffer` fails at import time — already enumerated above (only `tests/unit/test_buffer_persistence.py`, deleted in the same task). |
| `"buffer_entries"` removed from `_STOCKBOT_TABLES` | `tests/unit/test_init_db_script.py::test_init_db_creates_all_tables` would fail if the assertion subset still required `buffer_entries`; the test is edited in-pass to assert the new 2-table baseline. |
| `MemoryProjection` | Import failure in any consumer; grep confirms only `tests/unit/test_memory_schema.py` imports it. |
| `scripts/trace_tick.py` | Grep confirms zero references in `src/` / `tests/`. Script execution would fail at the shell — no caller catches that silently. |
| `_has_real_smart_money` inlined (A-094) | The existing `tests/agents/memory/test_writer_smart_money_seen.py` (and any sibling memory-writer integration test) asserts `BufferEntry.smart_money_seen` for both real-evidence and no-data cases. If the inlined expression diverges from the helper's semantics, those tests go red. |
| `log_cache_hit_to_state` + call site | Cache-callbacks tests cover the `report_cache_hit` structured-log emission, which remains untouched. The no-op state-write is the deleted half; no test asserts the state mutation (it was the bug the docstring describes). |
| `emit_analyst_totals` / `emit_analyst_header` | Grep confirms zero callers. If something monkey-patches the names without first importing them, it fails at attribute-access time during the next observability test. |
| Empty `src/deploy/` | No imports. If a future `from deploy import ...` exists, it fails at module-resolve time. |
| Empty `src/baselines/__init__.py` | The two surviving `src/baselines/*.py` modules still import under PEP 420 namespace packages. Any test that imports them transitively will fail at collection time if the namespace-package assumption is wrong. |
| §7.1 / §2.4 / §2.5 prose edits | Doc-only — no automated catch. Reviewer eyeballs the diff for fidelity to §8.1 / §8.6. |

**Cross-cutting silent-regression checks:**
- After Task 1, verify the ADK session-service path still creates the smaller table set:
  ```bash
  .venv/bin/python -c "from orchestrator.persistence import Base; print(sorted(Base.metadata.tables.keys()))"
  ```
  Expected: list **does not include** `buffer_entries` and is exactly the set Plan 04 expects to find.
- After Task 4, the structured `report_cache_hit` log entry must still be emitted on a cache hit — verified by the surviving cache-callbacks integration tests.

---

## Definition of done

All of the following must hold:

1. **Test suite green.**
   ```bash
   .venv/bin/python -m pytest tests/ -q
   ```
   exits 0.

2. **Lint clean** on every touched file:
   ```bash
   .venv/bin/python -m ruff check src/ tests/ scripts/
   ```

3. **Symbols absent.** All of the following greps return empty:
   ```bash
   grep -rn "BufferEntryRow\|save_buffer_entry\|load_recent_buffer" src/ tests/
   grep -rn "MemoryProjection" src/ tests/
   grep -rn "_has_real_smart_money" src/ tests/
   grep -rn "log_cache_hit_to_state" src/ tests/
   grep -rn "emit_analyst_totals\|emit_analyst_header" src/ tests/ scripts/
   grep -rn "attribution/writer\|attribution\.writer\|src/agents/attribution" src/
   grep -n "buffer_entries" src/lifecycle/initialise.py src/lifecycle/hard_reset.py
   ```

4. **Files absent.**
   ```bash
   test ! -e scripts/trace_tick.py
   test ! -e src/deploy
   test ! -e src/baselines/__init__.py
   test ! -e tests/unit/test_buffer_persistence.py
   ```

5. **Intent doc edits applied.** `intent.md` §7.1 says "shelved", §2.4 lists no-short as step 2, §2.5 has no "and empty `attribution/`" parenthetical.

6. **Each task landed as its own commit** with a `chore(audit):`, `refactor(audit):`, or `docs(audit):` subject line tagged with the finding ID. Nine commits total.

7. **`graph_delta.md` appended** with a dated entry summarising the symbol/file removals, per the project CLAUDE.md convention for structural changes.
