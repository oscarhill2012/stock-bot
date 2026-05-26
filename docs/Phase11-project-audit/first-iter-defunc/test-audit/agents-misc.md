# Test audit — agents miscellaneous (snapshot / contract / memory / loose)

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-misc.md`
**Test files in scope:** 16 (full list below)
**Tests collected from those files:** 67 (via `pytest <paths> --collect-only -q`)
**Findings:** 4 P0 · 5 P1 · 4 P2 · 1 P3

## Files in scope

Grouped by location — the layout-spread is itself a T8 finding (see P2-04).

- `tests/agents/` (loose) — 1 file
  - `tests/agents/test_isolated_failure.py`
- `tests/agents/memory/` — 1 file
  - `tests/agents/memory/test_writer_smart_money_seen.py`
- `tests/unit/` (root-level, mixed) — 7 files
  - `tests/unit/test_snapshot_persistence.py`
  - `tests/unit/test_memory_writer_agent.py`
  - `tests/unit/test_memory_compress.py`
  - `tests/unit/test_memory_eviction.py`
  - `tests/unit/test_memory_schema.py`
  - `tests/unit/test_embeddings.py`
  - `tests/unit/test_dedup.py`
  - `tests/unit/test_buffer_persistence.py`
- `tests/unit/agents/` — 1 file
  - `tests/unit/agents/test_llm_retry.py`
- `tests/unit/backtest/` — 1 file (touches snapshot + evidence_writer + memory + executor)
  - `tests/unit/backtest/test_wall_clock_leakage.py`
- `tests/integration/` — 4 files
  - `tests/integration/test_snapshotter.py`
  - `tests/integration/test_evidence_writer.py`
  - `tests/integration/test_memory_writer_integration.py`
  - `tests/integration/test_retry_smoke.py`

Out-of-scope but adjacent (verified — they exercise `orchestrator.persistence`, NOT `agents.contract.evidence_writer`): `tests/integration/test_evidence_persistence.py`, `tests/unit/test_evidence_index.py`, `tests/unit/test_evidence_row_persistence.py`, `tests/contract/test_evidence_schema.py`, `tests/unit/contract/test_*`, `tests/unit/agents/strategist/test_evidence_view*`, `tests/agents/analysts/test_evidence_callback.py`. These belong to the strategist / persistence / contract-schema audits.

## Summary

The retry + isolated-failure suite is the strongest in the consolidated area — `tests/unit/agents/test_llm_retry.py` and `tests/agents/test_isolated_failure.py` exercise real exceptions, assert on structured-log records by `kind`, and check both happy and exhausted branches; they are the model the rest of the area should aspire to (consistent with the source audit calling those two modules "clean"). The Snapshotter and EvidenceWriter tests, by contrast, are riddled with §A.7 silent-failure attractors: every `test_snapshotter_*` test either explicitly forces `spy_price = 0.0` via a stubbed exception or relies on `empty=True` history without ever asserting on the swallow, so the source P0-01 SPY-swallow regression would pass them all green — and there is no test at all for the cold-start anchor write of P0-02. The Memory suite is heavier on coverage but is anchored to two soon-to-be-deleted helpers (`MemoryProjection`, plus the unused `set_compress_llm` / `set_embedding_provider` setters that have no test callers but are about to be deleted in source). The two largest layout problems: (a) root-level `tests/unit/test_memory_*.py` files live outside the mirror tree (`tests/unit/agents/memory/`), and (b) writer-side tests are scattered across four different directories.

## Findings

### P0-01 · T4 missing surfacing test · Snapshotter SPY-fetch failure flat-lines the curve

- **Location(s):** new test needed (current closest: `tests/integration/test_snapshotter.py:47-83`, which *cements* the swallow rather than surfacing it)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P0-01
- **Confidence:** high
- **Description:**
  The source audit's headline P0 is `src/agents/snapshot/agent.py:60-74` swallowing every SPY-fetch exception into `spy_price = 0.0`. No test in the suite asserts that this failure mode *surfaces*. Worse, `test_snapshotter_accepts_iso_string_as_of` actively bakes the swallow into the contract: it does `with patch("data.get_price_history", side_effect=Exception("no network in test"))`, the docstring explicitly says "Snapshotter degrades to spy_price=0.0 on provider failure", and the test then proceeds without any assertion on `snap["spy_price"]` or any `caplog` check for a `kind="spy_fetch_failed"` warning. The wall-clock-leakage Snapshotter test (`tests/unit/backtest/test_wall_clock_leakage.py:130-142`) configures `fake_ticker.history.return_value = MagicMock(empty=True)`, which silently flows down the same "bars empty → spy_price stays 0.0" arm; again no positive assertion on `spy_price > 0`. The result is that the canonical §A.7 anti-pattern named in the source audit is *defended* by the suite, not caught.
- **Suggested action:**
  Add a new test that forces `get_price_history` to raise and asserts (i) the exception propagates / the wrapper logs a `kind="spy_fetch_failed"` record at WARNING, and (ii) `state["last_snapshot"]["spy_price"]` is `None` (or the call raised) rather than `0.0`. Pair with a happy-path test that fixes `bars[-1].close = 470.0` and asserts `snap["spy_price"] == 470.0` — neither shape currently exists.

### P0-02 · T4 missing surfacing test · Snapshotter live cold-start anchors are untested

- **Location(s):** new test needed
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P0-02
- **Confidence:** high
- **Description:**
  The source audit flags that `state["starting_capital"]` / `state["spy_start_price"]` are written via direct dict mutation on every tick where the key is absent (`agent.py:77-83`), with the live consequence that each fresh Cloud Run Job tick re-anchors and `bot_return_pct` is permanently ~0%. No test exercises the "tick 2 with a fresh ctx but the anchor row already exists in the DB" path. `tests/integration/test_snapshotter.py::test_snapshotter_writes_state` always runs with `state = {"tick_id": "tick-001"}` — i.e. tick 1 only, with no anchor pre-existing. There is no two-tick cold-start test that asserts `bot_return_pct` is computed against the *original* anchor (or, post-fix, the DB anchor row written by `lifecycle/initialise.py`) rather than against the current tick's `bot_total`.
- **Suggested action:**
  Add a test that runs the Snapshotter twice across two fresh `ctx` objects (mirroring the live "every tick = fresh process" topology) with the anchor row written between them, and asserts `snap["bot_return_pct"]` is non-zero on tick 2 when the portfolio value has changed. This must be authored as part of the P0-02 source fix PR.

### P0-03 · T5 mocks above the leaf · `test_snapshotter_writes_state` patches `yfinance.Ticker` instead of `data.get_price_history`

- **Location(s):** `tests/integration/test_snapshotter.py:31-37`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P0-01 (same code path)
- **Confidence:** high
- **Description:**
  The Snapshotter calls `from data import get_price_history; await get_price_history(...)` (`agent.py:62-70`), which is the registry-aware leaf documented in `docs/test-policy.md §A.5`. The test patches `yfinance.Ticker` instead, which only works because the yfinance provider happens to be the active price provider — a config edit would silently disable the mock. More importantly, the `MagicMock(empty=False, **{"__getitem__": lambda...})` shape rebuilds a fake pandas DataFrame instead of a real `PriceHistory` Pydantic object; if `get_price_history`'s return type changes, the test still passes because it never sees the Pydantic conversion. This is §E "Stubbing the wrong news provider" / §A.5 ("Stub at the leaf HTTP boundary, not above it") applied to price history.
- **Suggested action:**
  Reshape to monkeypatch `data.get_price_history` directly with a coroutine returning a real `PriceHistory(bars=[Bar(close=470.0, ...)])`. This makes the test config-independent and surfaces type drift, and aligns with the other Snapshotter test in `test_wall_clock_leakage.py` which already patches `data.get_price_history`-equivalent paths (it patches `sys.modules["yfinance"]` — also wrong, same fix applies).

### P0-04 · T5 mocks above the leaf · `test_snapshotter_uses_as_of` injects a fake `yfinance` module into `sys.modules`

- **Location(s):** `tests/unit/backtest/test_wall_clock_leakage.py:128-135`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P0-01
- **Confidence:** high
- **Description:**
  Same shape as P0-03: the test installs a fake module at `sys.modules["yfinance"]` to make `yfinance.Ticker` return an empty history. This is wrong on three counts. First, it patches the wrong layer (the leaf is `data.get_price_history`, not `yfinance.Ticker`). Second, it mutates a module-level global (`sys.modules`) without restoring it — that violates test-policy §A.6 ("Tests own their state") and leaks into subsequent tests in the same pytest session. Third, the empty-history path silently reaches the `spy_price = 0.0` arm, masking the P0-01 swallow; no assertion that the swallow is intended.
- **Suggested action:**
  Replace with `monkeypatch.setattr("data.get_price_history", AsyncMock(return_value=PriceHistory(bars=[...])))` — both pins the leaf and gets automatic teardown.

### P1-01 · T3 / T4 · `test_memory_writer_appends_buffer_entry` does not assert against the "unknown" decision_tag fallback

- **Location(s):** `tests/integration/test_memory_writer_integration.py:10-49`; `tests/agents/memory/test_writer_smart_money_seen.py` (both tests use deliberately-minimal decision dicts)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P1-03
- **Confidence:** high
- **Description:**
  The source audit P1-03 calls out `decision.get("decision_tag", "unknown")` in `writer.py:131-134` as a §A.7 attractor — when the strategist's required `decision_tag` is missing, the writer fabricates "unknown" and persists a junk row. No test exercises that path with the field *absent* and asserts a `KeyError` is raised (the prescribed surfacing behaviour). Worse, `tests/agents/memory/test_writer_smart_money_seen.py:14-29, 53-67` constructs `strategist_decision` with only `decision_tag`/`reasoning`/`thesis` — there's no `target_weights`, no `confidence`, no `close_reasons` — and the test passes anyway because the writer never validates the dict shape. The test asserts `smart_money_seen` only; it never asserts `decision_tag != "unknown"` on the resulting entry. After the P1-03 fix lands the writer should raise on missing `decision_tag` and this test (and the integration writer test) should assert positively that the buffer entry's `decision_tag` equals the input.
- **Suggested action:**
  Strengthen both tests to assert `state["memory_buffer"][-1]["decision_tag"] == "<expected>"` (the smart-money tests already use `decision_tag="test"`, so this is a one-line change). Add a new test that drops `decision_tag` from the input and asserts `KeyError` once P1-03 lands.

### P1-02 · T4 missing · No test that `memory_buffer` is hydrated from persistence on live cold-start (Spec C gap surfacing)

- **Location(s):** new test needed
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P1-02
- **Confidence:** medium
- **Description:**
  Source P1-02 documents that `memory_buffer` and `day_digest` rely on cross-tick state-dict carry-over which works in backtest (because of `driver.run`'s `state.update(updated_state)`) but is silently empty in live (each Cloud Run Job re-seeds them to `[]` and `""`). The audit prescribes adding a Phase-4 assertion that `memory_buffer` is non-empty after the first tick. No test exercises this. `tests/unit/test_buffer_persistence.py` round-trips the SQLAlchemy `save_buffer_entry`/`load_recent_buffer` pair in isolation but never connects them to the MemoryWriter — there is no end-to-end "writer persists on tick 1 → orchestrator hydrates → strategist sees a non-empty buffer on tick 2" test.
- **Suggested action:**
  When Spec C lands, add an integration test that runs two ticks back-to-back through the writer + load path, asserting tick 2's `state["memory_buffer"]` contains the tick-1 entry. Until then, file the gap so it does not get skipped over.

### P1-03 · T1 dead test · `test_memory_projection_*` exercises a class with no live callers

- **Location(s):** `tests/unit/test_memory_schema.py:35-47`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P2-03
- **Confidence:** medium
- **Description:**
  Two tests (`test_memory_projection_recent_limit`, `test_memory_projection_tag_frequency`) exercise `MemoryProjection.from_buffer`, which per the source audit is constructed only by these unit tests — no `src/` module imports it. The strategist prompt injects raw `memory_buffer` strings (`agents/strategist/prompts.py:91-92`), not a `MemoryProjection`. If P2-03's deletion lands, both tests go with it. The first two tests in the same file (`test_buffer_entry_rejects_long_summary`, `test_buffer_entry_accepts_max_summary`) are live and stay.
- **Suggested action:**
  Delete the two `test_memory_projection_*` functions in the same PR that deletes `MemoryProjection` per source-audit P2-03. Disposition is contingent on that source-fix PR.

### P1-04 · T1 dead-helper anchor (low signal, not full T1) · `set_compress_llm` / `set_embedding_provider` setters have no test callers — but no test files anchor them

- **Location(s):** no test files in scope (the source-audit P2-01 / P2-02 setters are not imported by any test)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` P2-01, P2-02
- **Confidence:** high
- **Description:**
  The source audit explicitly notes: `grep -rn "set_compress_llm" tests/` and `grep -rn "set_embedding_provider" tests/` both return zero. I re-verified this — confirmed, no test references them. This means the source-audit recommendation to delete those setters can land *without* a test-side cleanup; there are no zombie tests to remove. Recording this here so the consolidator does not file a "no test deletions needed" finding as a gap. (The injection paths the tests actually use are `compress(llm_fn=_stub_llm)` in `test_memory_compress.py:27,37,46` and `patch("agents.memory.writer.embed", new=AsyncMock(...))` in `test_memory_writer_integration.py:40`.)
- **Suggested action:**
  None on the test side. Confirming-rather-than-filing is the point of this entry.

### P1-05 · T2 parallel branches · `test_memory_writer_appends_buffer_entry` carries comments about removed bare-key `thesis` write

- **Location(s):** `tests/integration/test_memory_writer_integration.py:47-49`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-misc.md` (Band 4 — bare-key `thesis` migration, referenced in `writer.py:160-166`)
- **Confidence:** medium
- **Description:**
  The test contains a "NOTE (Band 4)" comment block but no actual assertion that `state["user:thesis"]` is *not* written by MemoryWriter. The writer's source has the same NOTE in code form but is also non-asserted. This is the test-side residue of a C2 collapse where the old bare-key branch has been removed but the test still narrates its absence rather than enforcing it. After the migration is fully landed, the comment should either become an assertion (`assert "user:thesis" not in state and "thesis" not in state`) or be deleted entirely.
- **Suggested action:**
  Add the explicit negative assertion in the same PR that removes the Band-4 NOTE from `writer.py:160-166`. The comment in isolation is dead documentation.

### P2-01 · T8 layout · Root-level `tests/unit/test_memory_*.py` files sit outside the mirror tree

- **Location(s):** `tests/unit/test_memory_compress.py`, `test_memory_eviction.py`, `test_memory_schema.py`, `test_memory_writer_agent.py`, `test_embeddings.py`, `test_dedup.py`, `test_buffer_persistence.py`
- **Source-audit cross-ref:** n/a (layout)
- **Confidence:** high
- **Description:**
  Per `docs/test-policy.md §B`, unit tests live under `tests/unit/<module-mirror>/`. The source modules are `src/agents/memory/compress.py`, `src/agents/memory/dedup.py`, `src/agents/memory/embeddings.py`, `src/agents/memory/schema.py`, `src/agents/memory/writer.py` — so the mirror is `tests/unit/agents/memory/test_<thing>.py`. Today these seven files all sit at the root of `tests/unit/` next to backtest, orchestrator, and provider tests; a fresh reader looking for memory tests has to grep the whole tree. `tests/unit/agents/memory/` does not exist; `tests/agents/memory/` does and contains exactly one file (`test_writer_smart_money_seen.py`). Both directories should consolidate at `tests/unit/agents/memory/`.
- **Suggested action:**
  In a single move-only PR, relocate the seven `tests/unit/test_memory_*.py` files plus `test_embeddings.py` and `test_dedup.py` and `test_buffer_persistence.py` to `tests/unit/agents/memory/`, and move `tests/agents/memory/test_writer_smart_money_seen.py` to the same directory. `tests/agents/` would then contain only `test_isolated_failure.py`, which is itself loose-agent-level.

### P2-02 · T8 layout · `tests/agents/test_isolated_failure.py` mixes the loose-agents-level tree with the mirror-style `tests/unit/agents/`

- **Location(s):** `tests/agents/test_isolated_failure.py` vs `tests/unit/agents/test_llm_retry.py`
- **Source-audit cross-ref:** n/a (layout)
- **Confidence:** medium
- **Description:**
  `agents/isolated_failure.py` and `agents/llm_retry.py` are sibling modules (both loose agent-level helpers, both clean per source audit, both unit-testable). Their tests live in two different trees: one in `tests/agents/`, one in `tests/unit/agents/`. The retry test sits in the mirror tree as recommended by §B; the isolated-failure test does not. Consolidating both under `tests/unit/agents/` removes the asymmetry.
- **Suggested action:**
  Move `tests/agents/test_isolated_failure.py` → `tests/unit/agents/test_isolated_failure.py` in the same PR as P2-01.

### P2-03 · T3 · `test_evidence_writer_no_db_is_noop` asserts only completion + non-call

- **Location(s):** `tests/integration/test_evidence_writer.py:97-108`
- **Source-audit cross-ref:** n/a (no source finding, but matches §A.7 / §E "asserting only on counts")
- **Confidence:** low
- **Description:**
  The test asserts `events == []` and that `state.__getitem__` / `state.get` are never called. That confirms the short-circuit but never confirms that the DB *wasn't* written to (because there is no DB). It's adequate as-written but illustrates the §E pattern — combine the absence-of-event assertion with at least one positive check that no row exists in a DB the test set up. Low priority; mostly noted because this is the "writer with db_session=None" branch and the absence-test pattern doubles as a hygiene example.
- **Suggested action:**
  Optional: add a separate test that constructs the writer with a real `db_session`, supplies `state` containing evidence, and asserts the DB has rows after running. The current `test_evidence_writer_persists_both_row_types` already does this — so this finding is mostly redundant with that and is filed only for completeness.

### P2-04 · T8 layout · Snapshotter / EvidenceWriter / MemoryWriter writer-side tests spread across four directories

- **Location(s):** `tests/integration/test_snapshotter.py`, `tests/unit/test_snapshot_persistence.py`, `tests/integration/test_evidence_writer.py`, `tests/integration/test_memory_writer_integration.py`, `tests/unit/backtest/test_wall_clock_leakage.py`
- **Source-audit cross-ref:** n/a (layout)
- **Confidence:** medium
- **Description:**
  The three writer-side agents (Snapshotter, EvidenceWriter, MemoryWriter) are co-located in `src/agents/{snapshot,contract,memory}/` and share an architectural concern (final-phase writers that consume contract-bearing state and persist rows). Their tests, by contrast, are scattered: integration tests in `tests/integration/`, persistence round-trip in `tests/unit/`, and wall-clock leakage tests in `tests/unit/backtest/` (despite testing agents, not the backtest driver). `tests/unit/backtest/test_wall_clock_leakage.py` in particular imports `agents.snapshot.agent.SnapshotterAgent`, `agents.contract.evidence_writer.EvidenceWriter`, and `agents.memory.writer.MemoryWriter` directly — it's an agent-behaviour test that happens to assert a property motivated by the backtest. Its location is wrong by §B (touches one module ⇒ unit + mirror tree).
- **Suggested action:**
  Once the mirror-tree consolidation (P2-01, P2-02) lands, also: split `test_wall_clock_leakage.py` into per-agent test files under `tests/unit/agents/{snapshot,contract,memory,executor,strategist}/test_<x>_wall_clock.py` (or fold each block into the existing per-agent test file). Layout consolidation, not behavioural change.

### P3-01 · T8 cosmetic · `tests/unit/test_memory_writer_agent.py` is a two-assertion stub

- **Location(s):** `tests/unit/test_memory_writer_agent.py`
- **Source-audit cross-ref:** n/a
- **Confidence:** low
- **Description:**
  The whole file is `assert issubclass(MemoryWriter, BaseAgent)` and `assert MemoryWriter().name == "MemoryWriter"`. Neither assertion is wrong but they belong as one-liners in a richer per-agent test file (after the P2-01 mirror-tree consolidation, this would naturally fold into `tests/unit/agents/memory/test_writer.py`). Stand-alone files of two trivial assertions add discovery cost without test coverage.
- **Suggested action:**
  Fold the two assertions into the consolidated `tests/unit/agents/memory/test_writer.py` (which will exist after P2-01 lands) and delete the file. Cosmetic.
