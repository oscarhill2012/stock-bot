# Test audit — src/observability/

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/observability.md`
**Test files in scope:** 13 (full list below)
**Tests collected from those files:** 79 (via `pytest tests/unit/observability/ tests/unit/test_trace_writer.py tests/unit/test_trace_writer_exception_logging.py tests/unit/test_trace_maybe_noop.py tests/unit/test_llm_trace_callbacks.py --collect-only -q`)
**Findings:** 1 P0 · 2 P1 · 3 P2 · 1 P3

## Files in scope

Grouped by location.

- `tests/unit/observability/` — 9 files
  - `__init__.py`
  - `test_drain.py`
  - `test_lifecycle_logger.py`
  - `test_log_handler.py`
  - `test_metric_exporter.py`
  - `test_otel_setup.py`
  - `test_span_exporter.py`
  - `test_terminal_log.py`
  - `test_trace_writer_datetime.py`
- `tests/unit/` (loose root-level — should sit under `tests/unit/observability/` per §B) — 4 files
  - `test_trace_writer.py`
  - `test_trace_writer_exception_logging.py`
  - `test_trace_maybe_noop.py`
  - `test_llm_trace_callbacks.py`

Adjacent files that exercise observability seams from a different subsystem's perspective (not in primary scope, mentioned for the consolidator):
- `tests/unit/backtest/test_driver_per_tick_rebuild.py` — patches `backtest.driver.install_observability` and constructs a `TraceWriter` to drive `driver._run_one_tick`. Belongs to the backtest audit.
- `tests/unit/backtest/test_reporting_obs_aggregation.py` — pins the producer/consumer contract between `observability.exporters` / `log_handler` and backtest reporting. Cross-subsystem; belongs to the backtest audit.
- `tests/analysts/fundamental/test_joiner.py`, `tests/analysts/news/test_joiner.py`, `tests/unit/agents/strategist/test_validation_callback.py` — incidentally import `emit_analyst_summary` or `_trace_maybe` as part of analyst tests; belong to the agents audit. Their presence confirms `emit_analyst_summary` is genuinely the live surface (which strengthens the dead-code call on `emit_analyst_totals` / `emit_analyst_header`).

## Summary

The observability suite is structurally solid — 79 tests, all unit-layer, leaf-mocked, no live network or cache writes, with the OTEL stack exercised end-to-end through real `MeterProvider` / `TracerProvider` plumbing rather than over-stubbed. The single P0 is a test-policy §A.7 inversion: the existing `_trace_maybe` regression tests **pin the swallow-and-log behaviour as correct** rather than asserting the narrower exception coverage the source-audit P1-02 recommends, and the matching `usage_metadata` silent `pass` in `terminal_log.after_cb` is positively asserted to "not crash" — the test guarantees the silent failure stays silent. Top-level layout finding: four root-level `tests/unit/test_trace_*.py` / `test_llm_trace_callbacks.py` files belong under `tests/unit/observability/` per §B. No `scripts/trace_tick.py` tests exist, so the `_trace` vs `temp:_trace` drift (source P1-01) is not visible to the unit suite at all — it would have to be caught either by a new test or in the scripts audit.

## Findings

### P0-01 · T4 missing surfacing assertion · `_trace_maybe` swallow is pinned, not the narrower exception coverage the source audit prescribes

- **Location(s):** `tests/unit/test_trace_writer_exception_logging.py:33-66` (current test that pins the swallow); test gap also.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/observability.md` P1-02
- **Confidence:** high
- **Description:**
  Source-audit P1-02 flags `_trace_maybe` (trace.py:168-174) as a silent-failure attractor: it currently wraps `tw.snapshot(...)` in `try/except Exception: _LOGGER.exception(...)`, and the recommended fix is to "narrow [the] excepts to the specific failure modes that have been observed (e.g. `JSONEncodeError`) and let unexpected exceptions propagate". The existing test `test_trace_failure_logs_exception` does the opposite — it raises a `RuntimeError("simulated trace serialisation crash")` from a stand-in writer and asserts the call "Should not raise — the try/except keeps the run alive", then asserts a warning record is logged. After the source-audit narrowing PR lands, a `RuntimeError` (i.e. an unexpected exception) is supposed to propagate, not be swallowed — but this test will keep enforcing the swallow forever. Paired with the absence of any test asserting that an *unexpected* exception class (e.g. `RuntimeError`, `AttributeError` from a shape drift) propagates out of `_trace_maybe`, the suite actively blocks the source fix. Per `test-policy §A.7` and the `feedback_silent_failures_loud_tests` memory, this is the canonical "silent regression masquerading as a pinned contract" shape.
- **Suggested action:**
  After the source-fix PR for P1-02 lands, replace the `RuntimeError`-swallow assertion with two tests: (1) a `JSONEncodeError`-shaped failure logs and continues (current behaviour for the known-benign serialisation crash); (2) a `RuntimeError` from `tw.snapshot` *propagates*, so a future writer-side regression cannot hide behind the suppress. The current test as-written cannot survive the source narrowing — it pins the wrong invariant.

### P1-01 · T4 missing surfacing assertion · `make_observability_callbacks.after_cb` `usage_metadata` swallow positively asserted to "not crash"

- **Location(s):** `tests/unit/observability/test_terminal_log.py:300-320` (`test_after_cb_handles_missing_usage_metadata`), `:322-340` (`test_after_cb_handles_missing_start_stamp`), `:342-360` (`test_after_cb_handles_none_token_fields`).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/observability.md` P1-02 (second half — terminal_log.py:403-410 silent `pass`)
- **Confidence:** high
- **Description:**
  Source-audit P1-02 calls out the `usage_metadata` extraction in `terminal_log.py:403-410` as a fully-silent (no log) `try/except Exception: pass` whose failure mode would zero out every token-counter row in the analyst summary with zero diagnostics on a future ADK schema rename. The three tests cited pin the "no-crash" behaviour as correct: each asserts `result is None` and that the accumulator picks up the record anyway with `prompt_tokens=None`. None of them assert that a *shape-drift* failure (e.g. `usage_metadata` returning an unexpected non-`None`, non-namespace object that raises on `getattr`) surfaces via a `_LOGGER.warning`. After the source-audit fix lands the silent `pass` should at minimum convert to `_LOGGER.warning(...)`; right now no test asserts that warning fires, and the existing tests will keep passing if the warning is dropped again in a future refactor.
- **Suggested action:**
  Add `test_after_cb_logs_warning_when_usage_metadata_shape_unexpected` — pass an `llm_response` whose `usage_metadata` raises `AttributeError` (e.g. a `SimpleNamespace` with a property whose getter raises), use `caplog` to assert exactly one `WARNING` record on the relevant logger, and assert the record contains the analyst label + ticker so an operator can diagnose. Land alongside the source-fix PR. Confidence is `high` rather than `medium` because the test file already cares about token-extraction edge cases (three nearby tests cover the topic), so the gap is conspicuous.

### P1-02 · T1 dead tests (contingent) · no callers and no tests for `emit_analyst_totals` / `emit_analyst_header`

- **Location(s):** None to delete — there are no tests for either symbol.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/observability.md` P2-01
- **Confidence:** high
- **Description:**
  Confirms the source-audit P2-01 dead-code finding from the test-suite side: `grep -rn 'emit_analyst_totals\|emit_analyst_header' tests/` finds zero matches. The source-audit recommends deleting both functions; the test suite already does not defend them, so no test-side cleanup is required when the source-fix PR lands. Filing as P1 (not P2) because the consolidator should be told explicitly that the source deletion has no test dependency — a useful constraint for sequencing the cleanup PR.
- **Suggested action:**
  No action. Note in the consolidated audit that the source-side deletion can land without test edits.

### P2-01 · T8 layout · four `tests/unit/test_trace_*.py` / `test_llm_trace_callbacks.py` files belong under `tests/unit/observability/`

- **Location(s):** `tests/unit/test_trace_writer.py`, `tests/unit/test_trace_writer_exception_logging.py`, `tests/unit/test_trace_maybe_noop.py`, `tests/unit/test_llm_trace_callbacks.py`.
- **Source-audit cross-ref:** N/A (test-policy §B mirror-the-source rule)
- **Confidence:** high
- **Description:**
  Per test-policy §B, unit tests "live under `tests/unit/` mirroring the source tree (e.g. `src/agents/news/fetch.py` → `tests/unit/agents/news/test_fetch.py`)". The four files listed all exercise symbols from `src/observability/trace.py` (`TraceWriter`, `_trace_maybe`, `make_llm_trace_callbacks`) and should sit alongside their sibling `test_trace_writer_datetime.py` in `tests/unit/observability/`. The sibling already moved; the four root-level ones were not. The split-location pattern itself is a small layout-finding instance of "tests can live in three or four different directories — that itself is a layout finding" called out in the rubric §1.
- **Suggested action:**
  In the test-cleanup PR move all four files to `tests/unit/observability/` and run `pytest --collect-only` to confirm the count is preserved.

### P2-02 · T3 weak/circular assertion · `test_snapshot_appends_section` only asserts internal `_sections` keys

- **Location(s):** `tests/unit/test_trace_writer.py:10-15` (`test_snapshot_appends_section`), `:18-23` (`test_llm_pair_writes_in_and_out_sections`).
- **Source-audit cross-ref:** N/A (test-policy §A.7 / §E "asserting only on counts" anti-pattern)
- **Confidence:** medium
- **Description:**
  `test_snapshot_appends_section` calls `tw.snapshot(...)` and then asserts `list(tw._sections.keys()) == [...]`. That asserts the private dict was populated under the right key — which is essentially `assert snapshot writes to _sections`, a circular pin of the implementation. It doesn't assert that the *payload* survived snapshot intact (i.e. `tw._sections["01_fetch_news"]["data"] == {"AAPL": ...}`), so a future refactor that silently drops the payload while preserving the key would pass. The same shape applies to `test_llm_pair_writes_in_and_out_sections` — only the key presence is asserted, not that the prompt text and response text round-tripped. The "finalise writes JSON" test is fine — it round-trips through the file system, which is the contract.
- **Suggested action:**
  Strengthen both tests to read the section payload back and assert content, not just key presence. Pair the length assertion with at least one content assertion per the anti-pattern in `test-policy §E`. Low-urgency because there *is* a finalise round-trip test that covers the structural contract end-to-end.

### P2-03 · T6 wide-scope monkeypatch · `test_log_handler_attached_to_project_namespaces` mutates real top-level loggers

- **Location(s):** `tests/unit/observability/test_otel_setup.py:58-73`, broader pattern across the file via `_reset_for_tests()`.
- **Source-audit cross-ref:** N/A (test-policy §A.6 / §E "wide-scope monkeypatch.setattr on a class")
- **Confidence:** medium
- **Description:**
  Each `test_otel_setup.py` test calls `_reset_for_tests()` and then `install_observability()`, which attaches the handler to the live top-level `google_adk`, `agents`, `backtest`, `orchestrator`, `observability` loggers (verified by the test itself at line 67). These loggers are process-global; later-running tests that also use `caplog` on any descendant of those namespaces will pick up records from the still-attached handler unless the test's own `_reset_for_tests()` cleans them up. The handler-attachment side-effect is exactly what the assertion checks for, so it cannot be avoided — but `_reset_for_tests()` may or may not actually detach handlers after the test ends (the test does not assert it does). Per test-policy §A.6, tests own their state; here, the live module-level loggers are owned by the runtime and the test reaches into them. Severity is P2 because (a) the assertion is genuinely important — the source-audit calls out that all project namespaces get the handler — and (b) the tests at least call `_reset_for_tests()` explicitly, so the impact is bounded.
- **Suggested action:**
  Verify `_reset_for_tests()` actually `removeHandler`s the buffered handler from all five namespaces (`agents`, `backtest`, `orchestrator`, `google_adk`, `observability`) at teardown, or wrap it in a `@pytest.fixture(autouse=True)` that runs `_reset_for_tests()` in both setup and teardown. If teardown already cleans up, downgrade this to P3.

### P3-01 · T8 cosmetic · `test_terminal_log.py` mixes `class TestX` and module-level functions inconsistently

- **Location(s):** `tests/unit/observability/test_terminal_log.py:33` onwards uses `class TestFormatTokens` / `TestEmitAnalystSummary`; lines 671-746 (the four `test_emit_analyst_summary_*_retries_*` tests) are module-level functions covering the same surface.
- **Source-audit cross-ref:** N/A
- **Confidence:** high
- **Description:**
  Same file, same surface area (`emit_analyst_summary`), two organisational styles — `TestEmitAnalystSummary.test_*` for the original 11 tests and bare `test_emit_analyst_summary_*` for the retries-suffix block added later. Tests work fine; the inconsistency is purely cosmetic and is easy to fix in a single edit by moving the four retries tests into `class TestEmitAnalystSummary` (or moving everything to module-level, which is the simpler style most other files in the suite use). Worth noting because the file is otherwise well-organised and the inconsistency reads like an unfinished append.
- **Suggested action:**
  Consolidate the four retries tests into `TestEmitAnalystSummary` (or split the existing classes back into module-level functions); next touch to the file.

## Notes for the consolidator

- **Cross-subsystem dependency — backtest audit owns the producer/consumer contract.** `tests/unit/backtest/test_reporting_obs_aggregation.py` pins how the per-tick JSON files written by `TickBufferedSpanExporter` / `TickBufferedLogHandler` are read back by `backtest.reporting`, and `tests/unit/backtest/test_driver_per_tick_rebuild.py` mocks `backtest.driver.install_observability`. The backtest audit needs to assert that side of the contract — this audit only covers the writers. If the backtest audit also files findings against `_drain_logs_cache_hits` reaching into `log_handler._buffer` (called out in the source-audit Notes), keep that finding out of observability scope.
- **`scripts/trace_tick.py` `_trace` vs `temp:_trace` drift (source-audit P1-01).** No `tests/unit/test_trace_*.py` file references `scripts.trace_tick`, so the silent degradation is invisible to the unit suite. Per the rubric this is the scripts audit's problem — file a T4 there for "no test asserts `scripts/trace_tick.py` actually seeds the writer at `state['temp:_trace']`" rather than here.
- **Rule 8 compliance: no test asserts the writers *don't* touch contract-bearing state.** Every observability test exercises a `temp:`-prefixed accumulator or a writer-private `_sections` / `_buffer`. None of them positively asserts the absence of a write to a contract key (e.g. `assert "user:positions" not in state after_cb fires`). The source audit calls Rule 8 compliance "good" because the code does honour it, but per the `feedback_silent_failures_loud_tests` memory the test suite ought to *enforce* the invariant rather than rely on inspection. This would normally be a T4 finding, but Rule 8 belongs to the contract-invariants suite (`tests/contract/`), not to per-subsystem unit tests, so it is mentioned here as a meta-observation rather than filed. Worth surfacing in the consolidated audit as "consider adding a contract test asserting observability writers never write to non-`temp:` keys".
- **OTEL stack tests bypass `BacktestSettings` and `tmp_path` correctly.** All exporter / handler / drain tests use `tmp_path` and `_reset_for_tests()` cleanly — no §A.2 violations, no `backtests/` writes, no real LLM. The suite scores well against the hard rules.
