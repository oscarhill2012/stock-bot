# Plan 12 — P3 sweep + orchestrator misc + Postgres assumption

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sweep the residual P3 tail from the 2026-05-26 codebase audit plus four explicitly-routed P2 items (A-050, A-080, A-081, A-091) that none of Plans 01–11 picked up. Most changes are one-liner deletes, doc fixes, or narrowed `except` clauses; the deliverable is a clean audit board with no severities above P3 remaining unaddressed.

**Architecture:** No structural changes. This plan is a tail sweep — group edits by file to minimise context-switching, prefer raising over silent degradation even for nits, and prefer plain `delete` over `keep with a TODO`. Where a P3 turns out to need a real design discussion, flag it as out-of-scope in §3 rather than expanding scope.

**Tech Stack:** Python 3.12, pytest, ruff, SQLAlchemy (for the Postgres-portability note only).

---

## 1. Goal + trust contract

### Goal
Land all remaining P3 audit items plus the four routed P2 items below, so the audit board has nothing above P3 left open and the P3 row reads "all closed or explicitly deferred with rationale".

### Trust contract
- **Trusts:** Plans 01–11 have all landed. In particular:
  - Smart-money is shelved as dormant scaffolding (Plan covering A-021/§8.1).
  - Bare-`positions` bridge is gone (Plan covering A-014).
  - Strategist legacy callback / evidence_view / `attribution/` are deleted (Plans covering A-023/A-025/A-028).
  - `STOCKBOT_TABLES` (Plan 04 renamed; underscore prefix dropped on move to `src/lifecycle/_tables.py`) is derived from `Base.metadata` (Plan covering A-011).
  - Phase-3 unused providers + dormant schemas are deleted (Plans covering A-036/A-082).
  - HandleInjectorPlugin is installed in live (Plan covering A-010/A-047).
  - `scripts/trace_tick.py` is deleted per §8.4 (Plan covering A-012).
- **Trusted by:** nothing — this is Plan 12 of 12.

### Title clarification
The original prompt for this plan referenced a "LooseToStrict mixin". On re-reading FINDINGS.md, **A-050** is `digest._fill_missing` silent neutral-fill — it is **not** the LooseToStrict item. The LooseToStrict mixin belongs to **A-051** (`TickerVerdict`/`LlmTickerVerdict` two-shape pattern), which is **explicitly out of scope** for Plan 12 (see §3). The four P2s actually routed here are A-050, A-080, A-081, A-091.

---

## 2. P3 inventory (plus four routed P2s)

Severities and IDs are quoted verbatim from `docs/audits/2026-05-26-codebase-audit/FINDINGS.md`.

| ID | Sev | Item | Implied change | Effort |
|---|---|---|---|---|
| A-050 | P2 | `digest._fill_missing` silent neutral-fill | Replace silent fill with structured WARNING that names the missing slot; gate on "synthetic prose is gone after A-016" (which Plan covering A-016 lands) so observers can again rely on `report=None ⇒ no data`. | small |
| A-080 | P2 | `last_snapshot` vs `last_executed_tick_id` parallel high-water marks | Collapse to a single high-water-mark read helper; keep both keys for now but add one `current_high_water(state)` accessor and route the four call sites through it. | small |
| A-081 | P2 | Live tick `BaseException` swallow | Narrow `except BaseException:` at `src/orchestrator/tick.py:259-270` to `except Exception:`; reuse backtest's `_log_exception_chain` helper. | one-liner |
| A-091 | P3 | `_check_live_tables_empty` Postgres `public.` assumption | Add a docstring note + raise a clear error if `db_url` schema is set to something other than the default; do **not** implement multi-schema support. Migration path documented inline. | one-liner + comment |
| A-083 | P3 | `headline_polarity_mean` alias nit | Confirm A-048 (P2 sibling) landed; if so, this is a no-op verification step. Otherwise delete the alias. | one-liner |
| A-084 | P3 | `_git_sha7` vs `_git_sha_full` duplication | Trust Plan 10 — already owned there (Plan 10 Task 6 Step 1). | n/a |
| A-085 | P3 | `build_telemetry_record_from_logs` orphan | Trust Plan 10 — already owned there (Plan 10 Task 6 Step 2). | n/a |
| A-086 | P3 | `state["thesis"]` / `user:thesis` residue | Grep + delete dead reads. | one-liner |
| A-087 | P3 | `TickState` unused | Delete the class + the import. | one-liner |
| A-088 | P3 | `_dispatch_app_name` over-abstraction | Trust Plan 09 — already owned there (Plan 09 Task 11). | n/a |
| A-089 | P3 | `BrokerMode._value2member_map_` private access | Swap to `BrokerMode(value)` constructor with explicit `ValueError` handling. | one-liner |
| A-090 | P3 (human-gated, **resolved keep**) | Cloud Scheduler shells | Per §8.5 — **no change**. Add a comment at the top of `src/lifecycle/scheduler.py` recording the §8.5 decision so future audits don't re-flag it. | comment only |
| A-092 | P3 | `BUFFER_MAX` unused | Delete the constant. | one-liner |
| A-093 | P3 | Triple structured-log emission pattern | Extract a `_emit_structured(logger, event, **fields)` helper in `src/agents/_logging.py` (new tiny module). | small |
| A-094 | P3 | `_has_real_smart_money` over-abstraction | Trust Plan 01 — already owned there. | n/a |
| A-095 | P3 | `log_cache_hit_to_state` no-op | Trust Plan 01 — already owned there. | n/a |
| A-096 | P3 | `report_cache.py` importlib gymnastics | Replace runtime importlib with a normal top-level import. | one-liner |
| A-097 | P3 | ~25 collected misc nits | Itemised in §4.4 below — most are deletes of empty packages / single-caller helpers / stale comments. | medium (in aggregate) |

---

## 3. Out-of-scope flags

These came up while scoping but deserve their own follow-up plan; **do not** roll them into Plan 12.

1. **A-051 — `TickerVerdict` / `LlmTickerVerdict` LooseToStrict mixin.** Despite the original Plan-12 title mentioning "LooseToStrict", this is a P2 schema-duplication item that needs a real mixin design and contract-shape decision. Owned by **Plan 13 — schema two-shape consolidation (A-051 + A-053 + A-054 + A-055)** which was commissioned to close this gap. (A-049 is owned by Plan 02 and is excluded from Plan 13.)
2. **A-093 — structured-log helper** *if* it turns out the three call sites use materially different field sets. The scoping in §4.3 assumes they don't. If implementation finds drift, stop and request a small dedicated plan.
3. **A-097 misc nits — T212 PAPER/LIVE URLs un-smoke-tested (F-broker-010/-011).** Per memory, project is pre-deployment; defer until broker work resumes. Flag as deferred in the test-strategy doc; do not write speculative smoke tests now.

---

## 4. Ordered changes

Group by file/module to minimise context-switching. Absolute paths throughout. Each task ends with a commit.

### Task 1 — A-091: document Postgres `public.` assumption

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/lifecycle/initialise.py:74-89`
- Test: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/test_init_db_script.py` (add one)

- [ ] **Step 1: Write the failing test.**

```python
# tests/unit/test_init_db_script.py — add
def test_check_live_tables_empty_rejects_non_default_schema(tmp_path):
    """Postgres non-public schema is not supported — document via explicit raise."""
    from src.lifecycle.initialise import _check_live_tables_empty, UnsupportedSchemaError

    # URL with explicit search_path other than public
    url = "postgresql+psycopg://u:p@h/db?options=-csearch_path%3Dtenant_a"

    with pytest.raises(UnsupportedSchemaError, match="public"):
        _check_live_tables_empty(url)
```

- [ ] **Step 2: Run the test to verify it fails.**

```bash
.venv/bin/python -m pytest tests/unit/test_init_db_script.py::test_check_live_tables_empty_rejects_non_default_schema -v
```

Expected: FAIL with `ImportError` for `UnsupportedSchemaError`.

- [ ] **Step 3: Add the error and the guard.**

```python
# src/lifecycle/initialise.py — near other error classes
class UnsupportedSchemaError(RuntimeError):
    """Raised when a Postgres URL targets a schema other than 'public'.

    Multi-schema deployment is a portability concern not a correctness one;
    if multi-schema support is needed, add a `schema` arg to the lifecycle
    helpers and qualify every table reference (currently 1 site in
    hard_reset.py:68 uses an explicit `public.` qualifier, which would also
    need to become `{schema}.`).
    """


def _check_live_tables_empty(db_url: str) -> None:
    """Verify the four StockBot tables are empty before init.

    Assumes the default Postgres schema (`public`). If the URL pins a
    different `search_path`, raise — see UnsupportedSchemaError.
    """
    # crude but sufficient: reject explicit non-public search_path
    if "search_path" in db_url and "public" not in db_url.split("search_path")[1]:
        raise UnsupportedSchemaError(
            "non-default Postgres schema detected in db_url; only 'public' is "
            "supported. See UnsupportedSchemaError docstring for the migration "
            "path."
        )

    engine = make_engine(db_url)
    # ... existing body unchanged
```

- [ ] **Step 4: Run the test to verify it passes.**

```bash
.venv/bin/python -m pytest tests/unit/test_init_db_script.py -v
```

- [ ] **Step 5: Commit.**

```bash
git add src/lifecycle/initialise.py tests/unit/test_init_db_script.py
git commit -m "docs(lifecycle): document Postgres public-schema assumption (A-091)"
```

---

### Task 2 — A-081: narrow live-tick `BaseException` catch

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/tick.py:259-270`

- [ ] **Step 1: Add a regression test in `tests/unit/orchestrator/test_tick_entrypoint.py`.**

```python
def test_run_tick_does_not_swallow_keyboard_interrupt(monkeypatch):
    """KeyboardInterrupt must propagate — the live catch was over-broad (A-081)."""
    from src.orchestrator.tick import run_tick

    def boom(*_a, **_kw):
        raise KeyboardInterrupt

    monkeypatch.setattr("src.orchestrator.tick._pipeline_run", boom)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run_tick(tick_id="t1", as_of="2026-05-26T00:00:00Z"))
```

- [ ] **Step 2: Run it — expected to fail (swallowed today).**

```bash
.venv/bin/python -m pytest tests/unit/orchestrator/test_tick_entrypoint.py::test_run_tick_does_not_swallow_keyboard_interrupt -v
```

- [ ] **Step 3: Narrow the catch.**

```python
# src/orchestrator/tick.py around 259-270
try:
    await _pipeline_run(...)
except Exception as exc:  # was: BaseException
    _log_exception_chain(exc)  # reuse from src/backtest/driver.py — move helper to src/observability/_exceptions.py first
    raise
```

If `_log_exception_chain` lives in `src/backtest/driver.py`, lift it to `src/observability/_exceptions.py` first (one tiny module move; backtest imports from new location).

- [ ] **Step 4: Verify all tick tests + the new one pass.**

```bash
.venv/bin/python -m pytest tests/unit/orchestrator/ tests/integration/backtest/ -v
```

- [ ] **Step 5: Commit.**

```bash
git add src/orchestrator/tick.py src/observability/_exceptions.py src/backtest/driver.py tests/unit/orchestrator/test_tick_entrypoint.py
git commit -m "fix(orch): narrow live-tick catch to Exception (A-081)"
```

---

### Task 3 — A-050: warn on `digest._fill_missing`

**Files:**
- Modify: `/home/oscarhill2012/Documents/Repository/StockBot/src/contract/digest.py:69-90`
- Test: `/home/oscarhill2012/Documents/Repository/StockBot/tests/unit/contract/test_digest.py`

- [ ] **Step 1: Add a test asserting a structured log + populated `feature_warnings`.**

```python
def test_fill_missing_emits_structured_warning(caplog):
    """A missing analyst slot is a pipeline bug per intent §7.1 — log it loudly (A-050)."""
    from src.contract.digest import build_digest

    # construct evidence with social slot deliberately missing
    ev = make_evidence_without_social("AAPL")
    with caplog.at_level("WARNING"):
        digest = build_digest(ev)

    assert any(
        rec.levelname == "WARNING" and "missing_analyst_slot" in rec.message
        for rec in caplog.records
    ), "expected structured WARNING when slot is missing"
    assert "social" in digest["AAPL"].feature_warnings
```

- [ ] **Step 2: Run — expected fail (silent today).**

```bash
.venv/bin/python -m pytest tests/unit/contract/test_digest.py::test_fill_missing_emits_structured_warning -v
```

- [ ] **Step 3: Add the warning + populate `feature_warnings`.**

```python
# src/contract/digest.py — inside _fill_missing
logger.warning(
    "missing_analyst_slot ticker=%s slot=%s — pipeline bug per §7.1; "
    "filling with is_no_data=True/report=None",
    ticker, slot,
)
verdict = AnalystVerdict(is_no_data=True, report=None, ...)
# also append slot name to TickerEvidence.feature_warnings so A-053 starts
# carrying signal (incidental cleanup; A-053 stays open for separate decision)
ev.feature_warnings.append(f"missing_slot:{slot}")
```

- [ ] **Step 4: Re-run test + the full contract suite.**

```bash
.venv/bin/python -m pytest tests/unit/contract/ -v
```

- [ ] **Step 5: Commit.**

```bash
git add src/contract/digest.py tests/unit/contract/test_digest.py
git commit -m "fix(contract): warn loudly on digest fill-missing (A-050)"
```

---

### Task 4 — A-080: collapse `last_snapshot` / `last_executed_tick_id`

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/_high_water.py`
- Modify: 4 call sites (grep for `last_snapshot` and `last_executed_tick_id`)

- [ ] **Step 1: Grep the call sites.**

```bash
grep -rn "last_snapshot\|last_executed_tick_id" src/ tests/ | tee /tmp/highwater.txt
```

- [ ] **Step 2: Write a tiny test pinning the helper's contract.**

```python
# tests/unit/orchestrator/test_high_water.py
def test_current_high_water_prefers_executed_tick_over_snapshot():
    """When both keys exist, executed_tick_id is the source of truth."""
    from src.orchestrator._high_water import current_high_water

    state = {"last_snapshot": "t1", "last_executed_tick_id": "t2"}
    assert current_high_water(state) == "t2"


def test_current_high_water_falls_back_to_snapshot():
    from src.orchestrator._high_water import current_high_water

    state = {"last_snapshot": "t1"}
    assert current_high_water(state) == "t1"


def test_current_high_water_returns_none_when_neither_set():
    from src.orchestrator._high_water import current_high_water
    assert current_high_water({}) is None
```

- [ ] **Step 3: Implement.**

```python
# src/orchestrator/_high_water.py
"""Single accessor for tick high-water marks (A-080).

Two parallel keys live in session state — `last_executed_tick_id` (written
by the executor) and `last_snapshot` (written by the snapshotter). They
usually agree but the executor's value is the authoritative "we committed
side effects up to here" mark. Read everything through this accessor so
future deduplication is a one-file change.
"""


def current_high_water(state: dict) -> str | None:
    """Return the latest tick_id at which side effects were committed."""
    return state.get("last_executed_tick_id") or state.get("last_snapshot")
```

- [ ] **Step 4: Route all call sites through the helper.** Replace direct `state.get("last_executed_tick_id") or state.get("last_snapshot")` reads. Do **not** change the writers; this is read-side consolidation only.

- [ ] **Step 5: Run the orchestrator + executor + snapshot tests.**

```bash
.venv/bin/python -m pytest tests/unit/orchestrator/ tests/integration/test_executor_with_fake_broker.py tests/integration/test_snapshotter.py -v
```

- [ ] **Step 6: Commit.**

```bash
git add src/orchestrator/_high_water.py tests/unit/orchestrator/test_high_water.py
git add -p   # stage call-site swaps interactively
git commit -m "refactor(orch): single high-water accessor for tick id (A-080)"
```

---

### Task 5 — P3 deletes in `agents-misc` (A-083, A-086, A-087, A-092)

Group all "delete dead symbol" P3s into one pass. Each is a one-liner; the commit summary calls them out individually.

(A-085 / A-088 / A-094 / A-095 were originally listed here but are owned by other plans — Plan 10 owns A-085, Plan 09 owns A-088, Plan 01 owns A-094 and A-095. They are not touched here. See the P3 inventory table.)

- [ ] **Step 1: Verify each symbol is truly orphaned.**

```bash
for sym in headline_polarity_mean TickState BUFFER_MAX; do
  echo "=== $sym ==="
  grep -rn "$sym" src/ tests/ scripts/ | grep -v "\.pyc"
done
grep -rn 'state\["thesis"\]\|user:thesis' src/ tests/ scripts/
```

For each: if the only hits are the definition + its own test file, delete is safe. If you find a real consumer, **stop and flag** — the audit may be stale.

- [ ] **Step 2: Delete each in turn (one commit per ID is fine, or batch into one commit titled "chore: P3 dead-code sweep").**

For A-083: confirm Plan covering A-048 already removed `headline_polarity_mean_7d`; if it left the non-suffixed alias, delete it now.

For A-086: delete reads of `state["thesis"]` / `state["user:thesis"]` — Plan covering A-014 already removed the bare-positions equivalent; thesis residue is the same pattern.

- [ ] **Step 3: Run the full unit suite.**

```bash
.venv/bin/python -m pytest tests/unit/ -v
```

- [ ] **Step 4: Commit.**

```bash
git commit -m "chore: P3 dead-code sweep (A-083,A-086,A-087,A-092)"
```

---

### Task 6 — (removed: A-084 is owned by Plan 10.)

---

### Task 7 — A-089: drop `BrokerMode._value2member_map_` private access

**Files:** `/home/oscarhill2012/Documents/Repository/StockBot/src/orchestrator/` (grep)

- [ ] **Step 1: Swap the access.**

```python
# before
mode = BrokerMode._value2member_map_.get(raw, BrokerMode.PAPER)

# after
try:
    mode = BrokerMode(raw)
except ValueError:
    raise ValueError(f"unknown BrokerMode {raw!r}; valid: {[m.value for m in BrokerMode]}")
```

(No silent default — surface the typo.)

- [ ] **Step 2: Test exists or add one asserting the raise.** Run.

- [ ] **Step 3: Commit.**

```bash
git commit -m "fix(orch): surface unknown BrokerMode instead of silent default (A-089)"
```

---

### Task 8 — A-090: record §8.5 keep-decision in `scheduler.py`

**Files:** `/home/oscarhill2012/Documents/Repository/StockBot/src/lifecycle/scheduler.py`

- [ ] **Step 1: Add a module docstring.**

```python
"""Cloud Scheduler shells (A-090, audit §8.5 — keep).

These functions are intentional scaffolding for the Cloud Scheduler
deployment path. The 2026-05-26 audit flagged them as P3 dead-code; the
human gate decision (intent.md §8.5) is **keep** because Cloud Scheduler
is the planned deployment topology. Do not delete without revisiting §8.5.
"""
```

- [ ] **Step 2: Commit.**

```bash
git commit -m "docs(lifecycle): record §8.5 keep-decision for scheduler shells (A-090)"
```

---

### Task 9 — A-093: extract structured-log helper

**Files:**
- Create: `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/_logging.py`
- Modify: 3 call sites identified in F-agents-misc-014

- [ ] **Step 1: Grep the three sites and confirm field-set parity.**

```bash
grep -rn "structured_log\|emit_structured\|log_structured" src/agents/
```

If field sets diverge meaningfully, **stop** and flag per §3 out-of-scope rule.

- [ ] **Step 2: Write helper + test.**

```python
# src/agents/_logging.py
import json
import logging


def emit_structured(logger: logging.Logger, event: str, **fields) -> None:
    """Emit a single-line JSON log record under a stable `event=` key.

    Used by analysts/strategist/executor to keep observability records
    parseable by the trace tooling. Prefer this over ad-hoc f-strings.
    """
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, default=str))
```

- [ ] **Step 3: Route the three sites.** Run the agent unit suite.

- [ ] **Step 4: Commit.**

```bash
git commit -m "refactor(agents): extract emit_structured log helper (A-093)"
```

---

### Task 10 — A-096: replace `report_cache.py` importlib gymnastics

**Files:** `/home/oscarhill2012/Documents/Repository/StockBot/src/agents/analysts/report_cache.py`

- [ ] **Step 1: Identify what the importlib call resolves to at runtime.** Likely a circular-import workaround. If circular, fix the cycle structurally; if not, switch to top-level import.

- [ ] **Step 2: Run analyst tests.**

```bash
.venv/bin/python -m pytest tests/unit/agents/analysts/ -v
```

- [ ] **Step 3: Commit.**

```bash
git commit -m "refactor(analysts): drop importlib gymnastics in report_cache (A-096)"
```

---

### Task 11 — A-097 misc-nits sweep

The audit lists ~25 nits under A-097. Treat each as a one-liner. Group by file. The list below is exhaustive per FINDINGS.md:625.

For each item: confirm dead via grep, delete (or doc-fix), run the relevant test slice, commit.

- [ ] **A-097.a — empty `src/deploy/`:** delete the directory.
- [ ] **A-097.b — legacy `emit_analyst_totals` / `_header`:** delete (F-ops-003).
- [ ] **A-097.c — `get_handles`:** delete (F-ops-004).
- [ ] **A-097.d — `SPYMetrics` / `_metrics_from_series`:** delete (F-ops-005, D-012).
- [ ] **A-097.e — two-namespace tuple (F-ops-008):** simplify to single namespace.
- [ ] **A-097.f — missing config-loader tests (F-ops-009):** add one happy-path + one schema-violation test.
- [ ] **A-097.g — `config/README.md` missing `watchlist_smoke.json`:** add the row.
- [ ] **A-097.h — empty `src/backtest/baselines/__init__.py`:** if directory is empty, delete it; if it holds modules, leave the `__init__.py` empty.
- [ ] **A-097.i — EDGAR/pit_composite bare `except`:** narrow to `(httpx.HTTPError, ValueError)` or whatever's actually thrown; log on catch.
- [ ] **A-097.j — `timeguard.py` wall-clock counter (F-data-008):** swap to `time.monotonic()`.
- [ ] **A-097.k — per-domain `__init__.py` double-bookkeeping (F-data-009):** pick one bookkeeping site.
- [ ] **A-097.l — `quiver_http_timeout_seconds` (F-data-010):** confirm read; delete if dead.
- [ ] **A-097.m — politician_trades fmp/quiver dup (F-data-011):** dedupe per memory (politician_trades is disabled in fetcher — confirm before touching).
- [ ] **A-097.n — blanket `noqa: E402` (F-data-018):** scope to the one line that needs it.
- [ ] **A-097.o — `_trace_maybe` cross-package underscore import (F-risk_gate-007):** promote helper to a public name or duplicate the two lines.
- [ ] **A-097.p — `_build_memory_writer` indirection (F-orch-006):** inline.
- [ ] **A-097.q — duplicate session-service tests (F-orch-008, T-109):** delete the duplicate.
- [ ] **A-097.r — doc-only `__init__.py` (F-backtest-014):** leave as-is; record decision in a one-line comment.
- [ ] **A-097.s — stale "Band 4" comment (F-backtest-015):** delete comment.
- [ ] **A-097.t — `reporting.py` N/A-by-string (F-backtest-008):** swap sentinel string for `None` + `Optional[float]`.
- [ ] **A-097.u — `decision_writer.py` BaseAgent overhead (F-strategist-013):** if no ADK features used, demote to plain function.
- [ ] **A-097.v — `digest_defaults.py` single-dict module (F-contract-012):** fold the dict into `digest.py`.
- [ ] **A-097.w — `strategist_prompt.render_all_ticker_blocks` single caller (F-contract-014):** inline.
- [ ] **A-097.x — T212 PAPER/LIVE URL smoke tests (F-broker-010/-011):** **out of scope per §3.3** — record as deferred in `docs/audits/2026-05-26-codebase-audit/test-strategy.md`.
- [ ] **A-097.y — strategist enricher gap on `intent=None` (F-strategist-012):** add an explicit raise (silent-failure prevention).
- [ ] **A-097.z — joiner verdict/evidence consistency test (F-analysts-016):** add the missing assertion.
- [ ] **A-097.aa — `decision_tags` plumbing unread (F-strategist-006):** delete the unread plumbing.

After each item:

```bash
.venv/bin/python -m pytest <relevant-slice> -v
git commit -m "chore(A-097.<letter>): <one-line description>"
```

(Granular commits make bisecting easier than one mega-commit.)

---

## 5. Test strategy

Most P3 items are pure deletes or comment-only edits — they need no new tests beyond running the existing suite to confirm nothing breaks. Exceptions where a regression test is warranted:

- **A-050** (Task 3) — content assertion: warning emitted **and** `feature_warnings` populated. This is a real silent-failure fix; per §A.7 of test policy, a `pytest.raises(LogMatcher)` / `caplog` assertion is mandatory.
- **A-081** (Task 2) — `KeyboardInterrupt` propagation test (the bug-cementing risk is real: without the test the next over-broad catch will re-introduce the swallow).
- **A-089** (Task 7) — unknown-mode raise test (single line, but the point is "don't silently default").
- **A-091** (Task 1) — unsupported-schema raise test (documents the portability boundary as executable spec).
- **A-097.f** — config-loader happy-path + schema-violation tests.
- **A-097.i** — bare-`except` narrowing — add a test that the narrowed exception type still gets caught.
- **A-097.y** — `intent=None` raise test.
- **A-097.z** — joiner verdict/evidence consistency test (this is the gap, so writing it *is* the fix).

For every other task: run `pytest tests/<affected-slice> -v` after the change, then `ruff check src/` before committing.

---

## 6. Risks / silent-regression checklist

This plan is mostly low-risk one-liners. The non-trivial risks:

1. **Task 2 (A-081) — narrowing `BaseException`.** If anything in the live pipeline raises `BaseException` subclasses other than `KeyboardInterrupt`/`SystemExit` (very unusual), the narrowed catch will let them propagate. That's the correct behaviour, but verify no test relies on the swallow.
2. **Task 4 (A-080) — single high-water accessor.** If any call site relies on observing one key but not the other (e.g. "snapshot is newer than executed"), the accessor's preference order will silently change behaviour. Grep for `if state.get("last_snapshot")` style guards before swapping; if found, stop and reconsider.
3. **Task 5 — `state["thesis"]` deletes.** Plan covering A-014 should have already removed the bare-`positions` bridge; if any thesis-side reader still exists in production code, deleting could null out a real path. Verify via grep before deleting.
4. **Task 9 (A-093) — structured-log helper.** Only safe if the three sites use congruent field sets. If they don't, skip this task and re-route to its own plan (see §3.2).
5. **Task 11.j — `time.monotonic()` swap in `timeguard.py`.** Confirm nothing reads the counter as a wall-clock timestamp downstream (`monotonic()` returns arbitrary float seconds, not Unix epoch).
6. **Task 11.m — politician_trades dedup.** Per memory, politician_trades is disabled in fetcher. Touching its providers risks re-enabling the dead path. Confirm with the user before deleting any politician_trades module.
7. **Sequencing within Task 5 and Task 11.** Some deletes depend on prior plans landing (A-014, A-021, A-048). If a prior plan slipped, the grep in Step 1 of each task will surface a live consumer — stop and flag.

---

## 7. Definition of done

- [ ] All 10 active numbered tasks in §4 marked complete (Task 11 has 27 sub-items; Task 6 was removed because A-084 is owned by Plan 10).
- [ ] `docs/audits/2026-05-26-codebase-audit/FINDINGS.md` updated with a status line under each addressed ID (`Status: closed in plan-12 (commit <sha>)` or `Status: deferred — see plan-12 §3`).
- [ ] `.venv/bin/python -m pytest tests/ -v` is green.
- [ ] `.venv/bin/python -m ruff check src/` is clean.
- [ ] No new files added beyond the four explicitly created here: `src/orchestrator/_high_water.py`, `src/agents/_logging.py`, `src/observability/_exceptions.py` (lifted from `backtest/driver.py`), and one new test per Task in §5.
- [ ] Audit board P3 row reads "all closed or explicitly deferred with rationale in plan-12 §3".
- [ ] `graph_delta.md` appended with a dated entry summarising the structural changes (helper module additions, deletions of dead symbols).
- [ ] No commit amends. Every task is its own commit (or, for Task 5 / Task 11, well-scoped grouped commits).
