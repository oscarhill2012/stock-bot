# agents-misc — audit findings

Scope: `src/agents/attribution/`, `src/agents/memory/`, `src/agents/snapshot/`,
`src/agents/isolated_failure.py`, `src/agents/llm_retry.py`,
`src/agents/__init__.py`.

Methodology: per-file read, plus repo-wide grep for production references and
test references. Intent §7 treated as authoritative.

---

## F-agents-misc-001
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/attribution/` (entire directory)
- **Evidence:**
  ```bash
  $ ls -la src/agents/attribution/
  drwxr-xr-x. 1 oscarhill2012 oscarhill2012   0 May 23 09:50 .
  drwxr-xr-x. 1 oscarhill2012 oscarhill2012 242 May 25 20:05 ..
  ```
  Empty directory (no files, no `__init__.py`). Intent §2.5 explicitly notes
  "verified the directory exists but is empty … It is a placeholder for future
  trade-attribution work." Production grep finds only docstring references
  (`src/agents/strategist/decision_writer.py:60`,
  `src/agents/contract/evidence_writer.py:68`) describing "the style used in
  attribution/writer.py" — pointing at a file that does not exist.
- **Intent violated:** §5.5 (documentation drift), §2.5 note.
- **Suggested action:** delete the empty directory; correct the two stale
  docstring references that point at the non-existent `attribution/writer.py`.
- **Notes:** Zero code under the path, so the only liability is a misleading
  signpost in two docstrings.

## F-agents-misc-002
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/__init__.py` (single empty line)
- **Evidence:** File is 1 line long and empty (verified by `Read`).
- **Intent violated:** n/a (package marker — kept for import resolution).
- **Suggested action:** leave alone; mentioned only for completeness.
- **Notes:** No-op package init; not actually dead, just empty by design.

## F-agents-misc-003
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/memory/schema.py:22-38` (`MemoryProjection`)
- **Evidence:**
  ```bash
  $ grep -rn "MemoryProjection" src/ tests/ scripts/
  tests/unit/test_memory_schema.py:6: ...
  tests/unit/test_memory_schema.py:37: ...
  src/agents/memory/schema.py:22:class MemoryProjection(BaseModel):
  src/agents/memory/schema.py:34:    ) -> MemoryProjection:
  ```
  Only one production-side definition; only consumer is the schema's own unit
  test. Strategist prompt does NOT read `MemoryProjection` — the template
  substitutes `{memory_buffer}` / `{day_digest}` directly
  (`src/agents/strategist/prompts.py:130-131`).
- **Intent violated:** none directly — intent flags `memory_buffer` / `day_digest`
  persistence as deferred (Spec C). The `from_buffer` classmethod was the
  intended consumer.
- **Suggested action:** investigate — either delete (no current consumer) or
  acknowledge as Spec C scaffolding. The class adds no value today.
- **Notes:** Pair with F-agents-misc-008 (the test).

## F-agents-misc-004
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/memory/embeddings.py:6-13`
  (`set_embedding_provider`); `src/agents/memory/compress.py:9-15`
  (`set_compress_llm`)
- **Evidence:**
  ```bash
  $ grep -rn "set_compress_llm\|set_embedding_provider" src/ tests/ scripts/
  src/agents/memory/embeddings.py:10:def set_embedding_provider(fn) -> None:
  src/agents/memory/compress.py:12:def set_compress_llm(fn: ...) -> None:
  ```
  Both setters are defined but never called from anywhere — production or
  tests. Tests use `monkeypatch.setattr(...)` /
  `patch("agents.memory.writer.embed", ...)` /
  `compress(..., llm_fn=stub)` paths instead.
- **Intent violated:** §2.5 — over-abstraction (unused DI seam).
- **Suggested action:** delete both setters and the module-level
  `_embedding_provider` / `_compress_llm` slots that back them.
- **Notes:** Classic dead test-injection seam left over from earlier design.

## F-agents-misc-005
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/orchestrator/persistence.py:27-79`
  (`BufferEntryRow`, `save_buffer_entry`, `load_recent_buffer`)
- **Evidence:**
  ```bash
  $ grep -rn "save_buffer_entry\|load_recent_buffer\|BufferEntryRow" src/ scripts/
  src/orchestrator/persistence.py:27:class BufferEntryRow ...
  src/orchestrator/persistence.py:41:def save_buffer_entry ...
  src/orchestrator/persistence.py:59:def load_recent_buffer ...
  # (zero non-definition production hits)
  ```
  Only consumer is `tests/unit/test_buffer_persistence.py`. This is the
  Spec-C-deferred memory-persistence path; the MemoryWriter does not call
  these, and Phase 2/4 hydration ignores them. Listed here because the
  audit explicitly asks for cross-tick-dead findings on the memory layer.
- **Intent violated:** §2.5 ("Cross-tick persistence deferred to Spec C;
  today rebuilds from empty each tick"). Not a contradiction — but flag for
  triage: do we keep the persistence shell, or remove until Spec C designs
  it from scratch?
- **Suggested action:** investigate. Likely "keep as scaffolding" given the
  user's stated intent that this is deferred, not abandoned. If kept, the
  audit owner should formally annotate it as `# Spec C scaffolding —
  unwired pending §E design`.
- **Notes:** This is NOT inside the agents-misc module path, but it is the
  load-bearing persistence side of the `memory/` package — flagging here
  for human triage rather than handing off to the ops module audit.

## F-agents-misc-006
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/agents/snapshot/agent.py:60-74`
- **Evidence:**
  ```python
  spy_price = 0.0
  try:
      from data import get_price_history
      ...
      spy_hist = await get_price_history("SPY", ..., as_of=recorded_at, phase=tick_phase)
      if spy_hist.bars:
          spy_price = float(spy_hist.bars[-1].close)
  except Exception:  # noqa: BLE001 — defensive; never crash the tick
      spy_price = 0.0
  ```
  A bare `except Exception` swallows every failure and silently substitutes
  `spy_price = 0.0`. There is no log call, no warning, no observability
  event. Downstream, `spy_value_if_held` / `spy_return_pct` /
  `excess_return_pct` are all computed against 0.0 with the special-case
  divide guard `if spy_start else 0.0` (line 87), so a sustained provider
  outage silently degrades the equity-curve baseline to "SPY = flat" for
  the rest of the backtest.
- **Intent violated:** §2.5 invariant ("Snapshotter passes `as_of` to
  SPY price-fetch so backtest does not leak wall-clock prices into
  historical snapshots") — degradation to 0.0 is worse than leaking
  wall-clock, because it is silent. Also recurring user feedback class
  "silent failures are the recurring bug class" (per `MEMORY.md`).
- **Suggested action:** at minimum log `WARNING` with `exc_info=True` and
  structured `kind="snapshot_spy_fetch_failed"` so an outage is visible in
  obs. Consider raising on the first-tick anchor (`spy_start_price` write)
  because anchoring to 0.0 permanently breaks every subsequent return calc
  for the run.
- **Notes:** As-of is correctly threaded — the silent-failure is purely the
  swallow.

## F-agents-misc-007
- **Category:** test-gap
- **Severity:** P1
- **Location:** `tests/integration/test_snapshotter.py:26-44`
  (`test_snapshotter_writes_state`)
- **Evidence:**
  ```python
  with patch("yfinance.Ticker") as mock_yf:
      ...
      async for _ in snapper._run_async_impl(ctx):
          pass
  assert snap["bot_total_value"] == 10_000.0
  assert snap["tick_id"] == "tick-001"
  ```
  The patch target is `yfinance.Ticker`, but the production SPY fetch goes
  through `data.get_price_history(...)` (see snapshot/agent.py:62-70). The
  patch is a no-op. The test exercises the F-agents-misc-006 silent-failure
  path (provider raises → `spy_price = 0.0`) by accident, but only asserts
  `bot_total_value` and `tick_id` so the silent degradation is invisible.
  The second test (`test_snapshotter_accepts_iso_string_as_of`) deliberately
  patches `data.get_price_history` and exercises the same fall-through, but
  again only asserts the tick_id round-trip — no assertion on `spy_price`
  being non-zero in any test.
- **Intent violated:** §2.5 invariant on PIT-correct SPY price; test
  policy §"silent-failure no test would catch".
- **Suggested action:** rewrite the first test to patch the real call site
  (`data.get_price_history`) and assert `snap["spy_price"] == 470.0`.
  Add a separate positive-signal test that asserts non-zero `spy_price`
  when the provider returns bars.
- **Notes:** Classic stale-patch — production refactored from yfinance to
  `data.get_price_history`, test was not updated.

## F-agents-misc-008
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/test_memory_schema.py:35-47`
  (`test_memory_projection_recent_limit`, `test_memory_projection_tag_frequency`)
- **Evidence:** `MemoryProjection` is the only consumer of these tests; see
  F-agents-misc-003 — the class has zero production consumers.
- **Intent violated:** test-policy "test only intended behaviour".
- **Suggested action:** delete if F-agents-misc-003 deletes
  `MemoryProjection`; keep if the class is retained as Spec C scaffolding.
- **Notes:** Couple to the F-agents-misc-003 decision.

## F-agents-misc-009
- **Category:** dead-test
- **Severity:** P2
- **Location:** `tests/unit/test_buffer_persistence.py` (entire file)
- **Evidence:** Tests `save_buffer_entry` / `load_recent_buffer` /
  `BufferEntryRow` which have no production consumer (see F-agents-misc-005).
- **Intent violated:** test-policy.
- **Suggested action:** investigate — same decision as F-agents-misc-005.
  If the persistence shell stays as Spec C scaffolding, this test is the
  only thing exercising the schema; if the shell is removed, delete the test.
- **Notes:** Couple to F-agents-misc-005.

## F-agents-misc-010
- **Category:** dead-test
- **Severity:** P3
- **Location:** `tests/unit/test_memory_writer_agent.py` (entire file)
- **Evidence:** Two assertions: `issubclass(MemoryWriter, BaseAgent)` and
  `mw.name == "MemoryWriter"`. These are tautological — the class declares
  `class MemoryWriter(BaseAgent)` and `name: str = "MemoryWriter"`.
- **Intent violated:** "code didn't crash" — not behaviour.
- **Suggested action:** delete; `test_memory_writer_integration.py` and
  `test_writer_smart_money_seen.py` cover the real behaviour.
- **Notes:** Smoke tests with no behavioural assertion.

## F-agents-misc-011
- **Category:** policy-mismatch
- **Severity:** P3
- **Location:** `src/agents/memory/writer.py:1-2` (module docstring),
  `src/agents/memory/writer.py:17-18` (`BUFFER_MAX = 24`,
  `BUFFER_EVICT_AT = 25`)
- **Evidence:** Intent §2.5 says "FIFO eviction to `day_digest` at ~25
  entries". Code constants name the threshold `BUFFER_EVICT_AT = 25`
  consistently. `BUFFER_MAX = 24` is unused — `grep -rn "BUFFER_MAX"
  src/ tests/` returns only the definition line. Cosmetic confusion: a
  reader sees two adjacent constants with overlapping names but only one
  is used.
- **Intent violated:** n/a (cosmetic).
- **Suggested action:** delete `BUFFER_MAX` (dead constant) or rename
  `BUFFER_EVICT_AT` to make the intent unambiguous.
- **Notes:** Nit.

## F-agents-misc-012
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/snapshot/agent.py:125-149`,
  `src/agents/memory/writer.py:153-192` (paired in-tick mutation + yielded
  state_delta event for the SAME keys)
- **Evidence:** Both agents do the same dance: assign
  `state[k] = v` directly AND yield an Event with
  `state_delta={k: v}`. Both modules contain near-identical block comments
  explaining the "InMemorySessionService merges via Event" rationale and
  cross-reference each other. Intent §5.4 records the snapshot
  `last_snapshot` paired direct write as "defensive belt-and-braces (out
  of A1 scope)". The MemoryWriter case is the same shape, not separately
  listed.
- **Intent violated:** §5.3 / §5.4 are aware of the snapshot+executor
  paired-write debt; memory/writer.py is the third instance, undocumented.
- **Suggested action:** consolidate-with-snapshot / executor — same
  belt-and-braces cleanup. Pick one channel and stick to it across all
  three after-callbacks.
- **Notes:** Not a P0 because the consequence is duplicate writes, not
  divergent writes — both channels carry the same payload.

## F-agents-misc-013
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/agents/memory/writer.py:21-56` (`_has_real_smart_money`)
- **Evidence:** A 35-line helper handles "evidence row may be dict OR
  pydantic" defensively, including a nested same-pattern for the `verdict`
  inside. Per intent §7.1, smart_money is registered and always emits a
  canonical no-data shape; downstream consumers may assume the key
  exists. The dual-shape handling is a real concern (model_validate vs
  raw dict from DatabaseSessionService round-trip), but the function size
  is disproportionate to its single use site.
- **Intent violated:** §2.5 (memory is deferred); §7.1 (smart_money is
  guaranteed registered).
- **Suggested action:** refactor — consider a tiny helper
  `_as_dict(verdict)` shared with the other dual-shape readers (executor
  has similar code), or accept the duplication and shrink to 5 lines.
- **Notes:** Not load-bearing; flag for the dedupe pass.

## F-agents-misc-014
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/isolated_failure.py:92-116` vs
  `src/agents/llm_retry.py:574-589` (`_log_retry`) and
  `src/agents/llm_retry.py:621-645` (`_log_exhausted`)
- **Evidence:** All three call sites independently build a structured
  WARNING/ERROR log with the same dual-channel pattern: message-string +
  `extra={"kind": ..., ...}`. `llm_retry.py:573` even contains the
  comment "Same shape applied to `agents.isolated_failure.branch_failed`
  for the same reason." Three near-identical implementations of one logging
  convention.
- **Intent violated:** n/a (working as intended; just duplicated).
- **Suggested action:** consider extracting a `_emit_structured(...)`
  helper. Low priority — the divergence risk is small since all three
  callsites are tight.
- **Notes:** Mentioned because the audit asks for cross-cutting wrapper
  dedupe.

## F-agents-misc-015
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/agents/llm_retry.py:170-175` (`_is_schema_error`)
- **Evidence:**
  ```python
  try:
      from pydantic import ValidationError
      if isinstance(exc, ValidationError):
          return True
  except ImportError:
      return False
  ```
  Pydantic is a hard project dependency. The defensive `except ImportError:
  return False` silently downgrades every schema-class retry to "not
  retryable" if pydantic vanishes. The module docstring even calls
  pydantic a hard dep ("Defensive import — Pydantic is a hard project
  dependency, but we mirror the import-guard style…"). Mirror-style
  defensive imports are also in `_find_validation_error:206-211` and
  `_format_schema_error_for_llm:246-272`.
- **Intent violated:** §2.5 invariant ("`pydantic.ValidationError` … with
  independent per-class attempt budgets and backoff").
- **Suggested action:** investigate — either raise on ImportError (loud
  failure) or remove the guard (let the import error propagate naturally,
  which is the standard pattern for hard dependencies). The current code
  silently masks a misconfigured environment.
- **Notes:** Aligns with the "Silent failures are the recurring bug class"
  feedback theme.

## F-agents-misc-016
- **Category:** policy-mismatch
- **Severity:** P3
- **Location:** `src/agents/memory/writer.py:131` (timestamp construction
  using `entry_ts`)
- **Evidence:**
  ```python
  raw_as_of = state.get("as_of")
  entry_ts = resolve_as_of(raw_as_of, allow_wallclock=True, site="memory/writer")
  ```
  `MEMORY.md` records "as_of boundary coercion is mandatory — every read
  of `state["as_of"]` uses `resolve_as_of`, every datetime write to state
  ISO-stringifies first". The MemoryWriter correctly calls `resolve_as_of`,
  and `model_dump(mode="json")` ISO-stringifies the timestamp before
  publishing (line 158). Policy is met. No finding — listed only because I
  checked and want to record that the path is clean.
- **Intent violated:** none.
- **Suggested action:** none.
- **Notes:** Confirms compliance.

---

## Cross-cutting summary

- **Dead code (P1):** four findings — empty `attribution/` dir, two unused DI
  setters (`set_compress_llm`, `set_embedding_provider`), unused
  `MemoryProjection`, unwired persistence shell `BufferEntryRow` +
  CRUD pair.
- **Silent failure (P1):** SPY fetch swallow in Snapshotter degrades
  equity-curve baseline to zero with no log. Top item for human attention.
- **Silent failure (P2):** `_is_schema_error` defensive ImportError swallow
  on a hard dep.
- **Test gap (P1):** Snapshotter test patches the wrong target and never
  asserts `spy_price`; the live failure path in F-agents-misc-006 has no
  coverage at all.
- **Dead tests (P2/P3):** three (`test_memory_projection_*`,
  `test_buffer_persistence.py`, `test_memory_writer_agent.py`).
- **Dedupe (P2):** triple paired-write pattern across snapshot / memory /
  executor; triple structured-log pattern across `isolated_failure` and
  `llm_retry`.

**Confirmed clean (no findings):** `IsolatedFailureWrapper` (only used by
news/fundamental per-ticker branches as intent requires);
`RetryingAgentWrapper` (wraps bare `LlmAgent` units only — strategist and
both per-ticker branches verified — composite-wrap rule honoured); the
`detect_repeat` / `compress` / `embed` core logic; per-tick state_delta
emission shape.

## Top three for human attention

1. **F-agents-misc-006** — silent SPY-fetch swallow in Snapshotter (P1
   silent-failure, plus its untested twin F-agents-misc-007).
2. **F-agents-misc-005** — unwired buffer-persistence shell
   (`BufferEntryRow` + CRUD) — needs an explicit "keep as Spec C
   scaffolding" or "remove until designed" decision. F-agents-misc-003 +
   F-agents-misc-008 + F-agents-misc-009 hinge on the same call.
3. **F-agents-misc-001** — delete empty `attribution/` dir and fix the
   two stale docstring references that point to the non-existent
   `attribution/writer.py`.
