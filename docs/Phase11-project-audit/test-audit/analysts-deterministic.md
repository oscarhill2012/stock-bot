# Test audit — `src/agents/analysts/{technical,social,smart_money}` + analyst plumbing

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` (primary); `docs/Phase11-project-audit/source-audit/SUMMARY.md` Open Question #1 (delete SmartMoney?)
**Test files in scope:** 21 (full list below)
**Tests collected from those files:** 164 (via `pytest <paths> --collect-only -q`)
**Findings:** 3 P0 · 8 P1 · 7 P2 · 2 P3

## Files in scope

Grouped by location — note that this subsystem's tests are spread across **four** parallel test trees (T8 layout finding in its own right).

- `tests/analysts/` (5 files) — `test_technical.py`, `test_smart_money.py`, `test_branch_composition.py`, `test_cache_callbacks_per_ticker.py`, `test_per_ticker_branch.py`
- `tests/agents/analysts/` (1 file) — `test_evidence_callback.py`
- `tests/agents/memory/` (1 file, peripheral) — `test_writer_smart_money_seen.py`
- `tests/agents/` (1 file, partially in-scope) — `test_output_caps_per_ticker.py` (mostly LLM-analyst, but touches `_common`-adjacent wrapper chain)
- `tests/unit/` (root) (9 files) — `test_analyst_heuristics.py`, `test_derive_smart_money_verdict.py`, `test_derive_social_verdict.py`, `test_derive_technical_verdict.py`, `test_extract_social_features.py`, `test_smart_money_fetch.py`, `test_smart_money_gate.py`, `test_social_analyst_run.py`, `test_social_fetch.py`
- `tests/unit/agents/analysts/` (5 files) — `test_analyst_fetch_as_of.py`, `test_chain_callbacks.py`, `test_report_cache_hash.py`, `test_report_cache_version.py`, `test_social_state_delta.py`, `test_technical_state_delta.py`
- `tests/unit/contract/extractors/` (3 files) — `test_technical.py`, `test_social.py`, `test_smart_money.py` (cross-cuts contract subsystem but exercises the deterministic-analyst extractors `_common` calls into)
- `tests/unit/orchestrator/test_temp_prefix_keys.py` (1 file, partial — pins `temp:<analyst>_data` source key)
- `tests/unit/orchestrator/test_pipeline_sequential_branches.py` (peripheral — covers topology, not analyst body)
- `tests/integration/test_analyst_pool.py` (peripheral — topology only)
- `tests/unit/data/models/test_smart_money.py` (1 file — exercises `SmartMoneyRaw` shape used by the analyst)

## Summary

The suite is mostly Tier-1 unit tests on the deterministic extractors and verdict-derivers, with good closed-vocabulary and feature-shape coverage. The dominant failure pattern is that nothing exercises the **wiring between fetch callback → `_run_async_impl` → shared `make_evidence_callback`**: every smart_money test exercises a fragment in isolation, so the source-audit P0 (`smart_money_data` vs `temp:smart_money_data` key drift) is invisible — the tests would all stay green even if the after-callback never reads anything. Layout is wider than the suite has any reason to be (four parallel test trees for one subsystem, including a misleading `tests/analysts/` root that mixes deterministic-analyst smoke tests with LLM per-ticker branch tests). SmartMoney coverage is heavy (≈ 35 tests across 7 files) — flagged throughout as conditional-T1 pending the "delete SmartMoney?" Open Question.

## Findings

### P0-01 · T4 missing surfacing test · no test catches the `smart_money_data` vs `temp:smart_money_data` writer/reader mismatch

- **Location(s):** new test needed (closest existing tests: `tests/unit/test_smart_money_fetch.py:test_fetch_callback_writes_ticker_first_smart_money_raw`, `tests/unit/test_smart_money_fetch.py:test_run_async_impl_emits_no_data_verdicts_when_data_empty`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P0-01
- **Confidence:** high
- **Description:**
  `smart_money_fetch_callback` writes the bare `state["smart_money_data"]` key (verified at `src/agents/analysts/smart_money/fetch.py:135`); `_run_async_impl` reads the bare key (line 117); but the shared `make_evidence_callback` (wired as `after_agent_callback`) reads `state["temp:smart_money_data"]` (`_common.py:98`). On any real ADK run, the after-callback's extractor receives `{}` for every ticker, and the resulting `AnalystEvidence.features` is empty — but a `neutral / is_no_data=True` verdict already sits in `smart_money_verdicts` from `_run_async_impl` (which read the correct bare key), so the pipeline still "succeeds". This is the textbook silent-failure attractor. The existing smart_money tests are blind to it because they only exercise:
  (a) the fetch callback in isolation — asserts `state["smart_money_data"]` is populated (and would in fact regress if the writer changed to `temp:`); and
  (b) `_run_async_impl` in isolation with a hand-seeded `state["smart_money_data"]`.
  No test invokes the full `before_agent_callback → _run_async_impl → after_agent_callback` chain and asserts that `state["smart_money_evidence"][i].features` carries non-empty, ticker-specific feature values. The test-policy §A.7 surfacing rule and the §G.7 `is_no_data=True` trap both apply directly.
  Additionally, the unit tests at `test_smart_money_fetch.py:71-107` and `test_smart_money_gate.py:57-140` actively **assert the bug-side key** (`ctx.state["smart_money_data"]`) — once the source-audit P0 fix renames to `temp:smart_money_data`, those existing tests will break unless the audit captures them as T2 candidates (P1-02 below).
- **Suggested action:**
  Add `tests/unit/agents/analysts/test_smart_money_full_chain.py` (or similar) with one async test that constructs an ADK `InvocationContext` with `InMemorySessionService`, runs `SmartMoneyAnalyst.run_async(ctx)` end-to-end with stubbed `get_public_figure_trades`/`get_notable_holders` returning at least one row, and asserts: (1) `state["smart_money_evidence"]` is a list of length `len(tickers)`; (2) every `ev.features["n_politicians"] > 0` (or whatever the stub seeds); (3) no `is_no_data=True` row for the seeded ticker; (4) `caplog` has no `branch_failed` record. Conditional on SmartMoney **not** being deleted (Open Question #1).

### P0-02 · T4 missing surfacing test · no test forces the `temp:smart_money_data` key contract via `make_evidence_callback`

- **Location(s):** new test needed (closest existing tests: `tests/agents/analysts/test_evidence_callback.py`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P0-01 (same root)
- **Confidence:** high
- **Description:**
  `tests/agents/analysts/test_evidence_callback.py` exercises `make_evidence_callback` with `analyst="technical"` only — every test seeds `state["temp:technical_data"]` directly. There is no parameterised version that exercises `analyst="smart_money"`, so the helper's documented read-pattern (`state[f"temp:{analyst}_data"]`, `_common.py:98`) is never tested against the smart_money name. A unit test that seeds `state["temp:smart_money_data"]` and asserts the resulting evidence row carries the expected features for that ticker would have caught the source bug immediately. The technical-only coverage in that file makes the evidence-callback look more thoroughly tested than it is.
- **Suggested action:**
  Extend `test_evidence_callback.py` to parameterise over `(analyst, extractor)` triples covering all three deterministic analysts — `technical`, `social`, `smart_money` — and assert evidence features are present and non-zero. Conditional on SmartMoney not being deleted.

### P0-03 · T3 / T4 missing surfacing test · per-ticker exception swallow in fetch callbacks is never asserted against

- **Location(s):** `tests/unit/test_social_fetch.py:117-136` (`test_social_fetch_empty_on_provider_failure`); no equivalent for technical, no equivalent for the smart_money per-provider catch
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P1-04 (filed there as P1; promoted to test-side P0 because the test that documents the swallow positively *codifies* the silent-failure pattern as expected behaviour)
- **Confidence:** high
- **Description:**
  `test_social_fetch_empty_on_provider_failure` asserts that when `get_social_sentiment` raises `RuntimeError("provider down")`, the callback "must NOT crash" and writes `{"snapshots": [], "aggregate_score": None}` into state. That is exactly the silent-failure pattern source-audit P1-04 calls out: a real provider outage is indistinguishable from "the ticker has no chatter". Per test-policy §A.7 and §G.7, the happy-path test should be **paired** with one that asserts the outage *surfaces* — currently the suite asserts swallowing is correct on every read of the same path. Neither `test_smart_money_fetch.py` nor `test_analyst_fetch_as_of.py` covers the same axis for technical or smart_money — the swallow path is silently endorsed by existing tests and silently uncovered by missing ones.
- **Suggested action:**
  Once source P1-04 is fixed (narrow `except`, propagate or emit `feature_warning`), invert this test: assert that an unmocked-out exception type **does** propagate (or that `caplog` records a structured `kind="provider_fetch_failed"` event with the ticker and provider name). Add the symmetric test for `technical_fetch_callback` and `smart_money_fetch_callback` — currently neither has any failing-provider coverage at all.

### P1-01 · T1 conditional dead tests · SmartMoney tests are wholesale-deletable if SmartMoney is deleted

- **Location(s):** `tests/analysts/test_smart_money.py` (5 tests), `tests/unit/test_derive_smart_money_verdict.py` (7 tests), `tests/unit/test_smart_money_fetch.py` (8 tests), `tests/unit/test_smart_money_gate.py` (3 tests), `tests/unit/contract/extractors/test_smart_money.py` (10 tests), `tests/unit/data/models/test_smart_money.py` (2 tests), `tests/agents/memory/test_writer_smart_money_seen.py` (2 tests) — **≈ 37 tests across 7 files**
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/SUMMARY.md` Open Question #1 (delete SmartMoney? — pipeline currently shelves it, source-audit P0/P1/P1 cluster all on this analyst)
- **Confidence:** high
- **Description:**
  Per the dispatch prompt's strategic question: if SmartMoney is deleted, all seven files above become T1 dead tests. The cluster includes the entire `_derive_smart_money_verdict` table-driven suite, the full fetch/gate dual coverage (already two parallel files — see P2-01), the contract extractor tests, and the memory-writer's smart_money-seen flag tests. None of these tests provides cross-subsystem value: `derive_smart_money_verdict` is not used by any other analyst, the gate behaviour is unique to this analyst, and `MemoryWriter.smart_money_seen` is a one-purpose flag. If the decision goes the other way (fix SmartMoney), the bulk of the tests remain valid but require the P0-01 / P1-02 reshapes; the source-audit P1-01 (Rule 1 `state_delta`) fix specifically will need a new test mirroring `test_technical_state_delta.py` / `test_social_state_delta.py`.
- **Suggested action:**
  Conditional disposition: if Open Question #1 resolves to "delete", land these test deletions in the same PR as the source-package deletion. If it resolves to "fix", reshape per P0-01/P0-02 and add a `test_smart_money_state_delta.py` mirroring the technical/social pair (also a T4 against source P1-01).

### P1-02 · T2 parallel-branch defenders · smart_money fetch/gate tests bake in the bare-key contract that the source-audit P0 fix deletes

- **Location(s):** `tests/unit/test_smart_money_fetch.py:71-107,239-269,375-411` (multiple tests assert `ctx.state["smart_money_data"]["AAPL"]`), `tests/unit/test_smart_money_gate.py:57-140`, `tests/unit/test_smart_money_fetch.py:298-329` (passes a hand-built `state` with the bare key into `_run_async_impl`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P0-01
- **Confidence:** high
- **Description:**
  Every one of these tests asserts the bare-key shape `state["smart_money_data"]` is written or readable. When source-audit P0-01 lands (renaming to `temp:smart_money_data` on both writer and reader), every one of these assertions breaks. They are not wrong in spirit — they pin the per-ticker SmartMoneyRaw shape, which is the lasting value — but the *key* they assert against is the loser side of the C4 contract violation. File now with a contingent disposition so the source-fix PR sweeps them in the same pass instead of trying to maintain two branches.
- **Suggested action:**
  In the same PR that lands source P0-01: search-and-replace `state["smart_money_data"]` → `state["temp:smart_money_data"]` in both files (plus `test_smart_money_fetch.py:298-329`'s hand-built state in `test_run_async_impl_emits_no_data_verdicts_when_data_empty`). Conditional on SmartMoney not being deleted (otherwise these are T1 deletions per P1-01).

### P1-03 · T4 missing surfacing test · no test forces `as_of` boundary coercion in social/smart_money agents

- **Location(s):** new tests needed (closest existing: `tests/unit/agents/analysts/test_analyst_fetch_as_of.py` covers the *fetch callback* side only, not the agent body side)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P1-02
- **Confidence:** high
- **Description:**
  Source P1-02 calls out that `social/agent.py:110` and `smart_money/agent.py:121` skip `resolve_as_of` and pass the raw `state.get("as_of")` (an ISO string in backtests because `DatabaseSessionService` can't serialise `datetime`s) to the extractor. The user-memory rule "`as_of` boundary coercion is mandatory" is binding. The only test that asserts boundary coercion is `tests/unit/contract/extractors/test_technical.py:test_relative_strength_rejects_string_as_of` — and that tests the *technical* extractor specifically, not the agent body. There is no test that calls `SocialAnalyst._run_async_impl` or `SmartMoneyAnalyst._run_async_impl` with `state["as_of"]` as an ISO string (the actual production shape) and asserts the agent passes a `datetime` to the extractor. The technical agent does this correctly (`resolve_as_of` at line 118-120); `tests/unit/agents/analysts/test_technical_state_delta.py` does not assert it either.
- **Suggested action:**
  Add three new tests (one per agent) that seed `state["as_of"]` as an ISO string and assert the extractor receives a `datetime` (via a spy extractor that records its `as_of` kwarg). The same test against `TechnicalAnalyst` would be a positive lock-in for the existing correct behaviour. Lands alongside source P1-02's fix.

### P1-04 · T4 missing surfacing test · no test catches the source P1-01 SmartMoney Rule-1 violation (direct state write)

- **Location(s):** new test needed (closest existing pair: `tests/unit/agents/analysts/test_technical_state_delta.py`, `tests/unit/agents/analysts/test_social_state_delta.py` — Rule-1 conformance tests for Technical and Social)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P1-01
- **Confidence:** high
- **Description:**
  Technical and Social each have a dedicated `test_*_state_delta.py` that pins Rule 1 (`_run_async_impl` must yield exactly one `Event` whose `actions.state_delta` contains `<analyst>_verdicts`). SmartMoney has **no** equivalent. Source-audit P1-01 documents that `SmartMoneyAnalyst._run_async_impl` currently writes verdicts directly to `state` and uses the `return; yield` no-op generator trick, in violation of Rule 1 — and that the bug is dormant only because the analyst is shelved. The Rule-1 test pair pattern is explicit and ready to copy. Its absence is the silent reason the source bug went undetected.
- **Suggested action:**
  Add `tests/unit/agents/analysts/test_smart_money_state_delta.py` mirroring `test_social_state_delta.py`. Conditional on SmartMoney not being deleted (Open Question #1).

### P1-05 · T3 weak assertion · `test_technical.py` and `test_smart_money.py` are construction-shape smoke tests with no behaviour coverage

- **Location(s):** `tests/analysts/test_technical.py` (2 tests), `tests/analysts/test_smart_money.py` (5 tests)
- **Source-audit cross-ref:** none directly — these are §A.7 / §E "did it import?" tests
- **Confidence:** high
- **Description:**
  Both files exclusively assert `isinstance(<singleton>, BaseAgent)`, `.name == "..."`, `.after_agent_callback is not None`, and `isinstance(.heuristics, ...)`. None of them invokes `_run_async_impl` or the fetch callback. Per test-policy §A.7 these are construction-shape tests with no behaviour coverage — they pass even when the analyst is completely broken (e.g. the SmartMoney source P0 fires inside `_run_async_impl`, the after-callback chain reads from the wrong state key, etc). They are not wrong, but they are decorative — and the fact that they live in `tests/analysts/` (a different tree from the *real* tests at `tests/unit/test_social_analyst_run.py` and `tests/unit/agents/analysts/test_technical_state_delta.py`) makes the suite *look* like it has analyst-body coverage when it doesn't.
- **Suggested action:**
  Delete `tests/analysts/test_technical.py` and `tests/analysts/test_smart_money.py` outright (the construction checks are subsumed by the existing `tests/unit/agents/analysts/test_*_state_delta.py` tests which exercise the body, and by `tests/integration/test_analyst_pool.py` which checks the singletons are wired into the pool). Or, less aggressively, merge them into the unit tree to remove the layout duplication (see P2-01). Conditional T1 if SmartMoney is deleted.

### P1-06 · T6 wide-scope `monkeypatch.setattr` of cache-config across all per-ticker tests

- **Location(s):** `tests/analysts/test_cache_callbacks_per_ticker.py:46-50,83-86,109-112,176-178`
- **Source-audit cross-ref:** none — pure test-policy hygiene
- **Confidence:** medium
- **Description:**
  Every test in this file monkeypatches the entire `agents.analysts.cache_callbacks.get_analysts_config` function with a `MagicMock`-returning lambda. Per test-policy §A.6 / §E "wide-scope monkeypatch", this hides a leaf seam — the cache directory and enabled flag should be set via a real fixture `AnalystsConfig` (or a `tmp_path`-backed config file plus `monkeypatch.setenv`). The current shape means that if the `get_analysts_config()` return-type shape changes — e.g. adds a required field — every test silently keeps passing because the `MagicMock` produces a spec-less stand-in.
- **Suggested action:**
  Replace the `MagicMock(cache=MagicMock(enabled=True, directory=str(tmp_path)))` pattern with a real `AnalystsConfig(...)` instance (or shared fixture) constructed from the live Pydantic model. Same fix applies to wherever else `get_analysts_config` is monkeypatched (one-pass `grep` would find).

### P1-07 · T3 weak assertion · `test_social_analyst_run.py` empty-data test asserts only on the empty-list

- **Location(s):** `tests/unit/test_social_analyst_run.py:125-138` (`test_run_async_impl_empty_social_data`)
- **Source-audit cross-ref:** ties into P1-04 above (test-policy §G.7 attractor — empty list == silent failure)
- **Confidence:** medium
- **Description:**
  The test asserts `state["social_verdicts"] == []` when `temp:social_data` is `{}`. This is technically correct as a degraded-path assertion, but it's the *happy*-path test for a no-tickers scenario, not a deliberate degraded-path exercise. The test does not set up `state["tickers"]` at all (the agent's loop iterates over `social_data.items()`), so the test passes equally well if the agent silently returned an empty list on every call. A happy-path test (with one ticker, one snapshot, asserting `is_no_data=False`) already exists at lines 70-86 — but it doesn't assert against the degraded path, and the degraded-path test doesn't assert that the agent intentionally produced the empty list rather than crashing-and-swallowing.
- **Suggested action:**
  Either strengthen this test to also assert `caplog` is empty of `branch_failed` records, or split into two tests with explicit `tickers=["AAPL"]` set both times, one with data and one without, and add a content assertion to each.

### P1-08 · T4 missing surfacing test · `_common.make_evidence_callback`'s parallel-branch wrapped-dict shape (source P2-05) is never exercised

- **Location(s):** new test needed (closest: `tests/agents/analysts/test_evidence_callback.py` only exercises the `list[dict]` shape)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-deterministic.md` P2-05
- **Confidence:** medium
- **Description:**
  Source P2-05 flags that `_common.py:106-110` carries a defensive `isinstance(raw, dict) and "verdicts" in raw` branch left over from when LlmAgent analysts wrote `{"verdicts": [...]}`. The test suite never exercises the wrapped-dict shape, so the source-audit cannot tell from the tests alone whether the branch is genuinely dead or whether it's a covered safety net. This is the test-side leverage point: if the dead-branch removal is to land, the *test* needs to assert that the helper now rejects the wrapped shape loudly (rather than silently coercing). Filed P1 not P2 because it's a precondition for the source P2-05 cleanup PR.
- **Suggested action:**
  Once source P2-05 lands, add one test that calls `make_evidence_callback` with `state[verdicts_state_key] = {"verdicts": [...]}` and asserts it raises (or, if the chosen disposition is "ignore", asserts the evidence list is empty and a warning is logged).

### P2-01 · T8 layout · four parallel test trees for one source subsystem

- **Location(s):** `tests/analysts/`, `tests/agents/analysts/`, `tests/unit/<flat>`, `tests/unit/agents/analysts/`
- **Source-audit cross-ref:** none — pure layout
- **Confidence:** high
- **Description:**
  The same source subsystem has tests scattered across:
  1. `tests/analysts/` (the original Phase-5 location, mixing deterministic and LLM-analyst tests)
  2. `tests/agents/analysts/` (single file — `test_evidence_callback.py`, just a different root)
  3. `tests/unit/` flat (the legacy `test_*_fetch.py`, `test_derive_*_verdict.py`, `test_extract_*_features.py` files)
  4. `tests/unit/agents/analysts/` (the "correct" location per test-policy §B — but contains only a handful of files)
  Per test-policy §B, unit tests should mirror the source tree (`src/agents/analysts/social/fetch.py` → `tests/unit/agents/analysts/social/test_fetch.py`). None of the four current locations honours that fully. This causes duplicate coverage (e.g. `test_smart_money_fetch.py` and `test_smart_money_gate.py` are near-duplicates with overlapping assertions; `tests/analysts/test_smart_money.py` and `tests/unit/agents/analysts/test_*_state_delta.py` both pin construction-shape).
- **Suggested action:**
  Consolidation pass: move every in-scope test into `tests/unit/agents/analysts/<analyst>/test_<aspect>.py`. Merge `test_smart_money_fetch.py` and `test_smart_money_gate.py` into one file. The layout reshape can ride alongside the smart_money source fix.

### P2-02 · T8 layout · `test_smart_money_fetch.py` and `test_smart_money_gate.py` are near-duplicates

- **Location(s):** `tests/unit/test_smart_money_fetch.py` (412 lines), `tests/unit/test_smart_money_gate.py` (141 lines)
- **Source-audit cross-ref:** none
- **Confidence:** high
- **Description:**
  Both files cover `smart_money_fetch_callback`. The "gate" file's three tests (`test_fetch_returns_none_when_no_activity`, `test_gate_passes_with_politician_trade`, `test_gate_passes_with_notable_holder`) duplicate the same-named tests in the "fetch" file almost verbatim — the latter is the original "fetch" file annotated with later Task 9 + Task 17 changes, and the "gate" file is a leftover from when there was a separate Phase-5 gating story. The Task 9 / Task 17 commentary at the top of the gate file even refers to the same migrations the fetch file documents. Both files monkeypatch the same two leaf functions and assert the same `result is None` and same SmartMoneyRaw-shaped payload.
- **Suggested action:**
  Delete `test_smart_money_gate.py` and merge any non-duplicate assertions into the consolidated `test_smart_money_fetch.py` (or its replacement after P2-01). Conditional T1 if SmartMoney is deleted.

### P2-03 · T3 weak assertion · `test_evidence_callback.py` test_empty_tickers_produces_empty_evidence

- **Location(s):** `tests/agents/analysts/test_evidence_callback.py:232-248`
- **Source-audit cross-ref:** §A.7
- **Confidence:** medium
- **Description:**
  Asserts `state["technical_evidence"] == []` when `tickers=[]`. Same shape as P1-07: an empty-input test that doesn't assert the degradation path is intentional. Less load-bearing than the social variant because the only way to reach an empty `tickers` list is an explicit configuration choice (no watchlist), and that branch is fine. Filed P2.
- **Suggested action:**
  Leave as-is, or add a comment noting this is the deliberate empty-watchlist path. Not worth the churn unless the file is touched for other reasons.

### P2-04 · T3 weak assertion · `tests/integration/test_analyst_pool.py` topology-only coverage

- **Location(s):** `tests/integration/test_analyst_pool.py` (4 tests)
- **Source-audit cross-ref:** none
- **Confidence:** medium
- **Description:**
  Asserts `isinstance(pool, ParallelAgent)`, `len(pool.sub_agents) == 3`, and the agent name set. None of the tests actually runs the pool. The same coverage lives at `tests/analysts/test_branch_composition.py` (which also only does topology) and at `tests/unit/orchestrator/test_pipeline_sequential_branches.py`. Three files for one topology assertion shape. Per test-policy §B, this is `tests/integration/` only if it *integrates* — currently it's a structural unit test in the wrong location. Filed P2 because the assertions themselves are correct and small.
- **Suggested action:**
  Either move to `tests/unit/orchestrator/` or merge with `test_pipeline_sequential_branches.py` (they overlap by ≈ 90 %).

### P2-05 · T3 weak assertion · `test_chain_callbacks.py:test_chain_before_empty_returns_none`

- **Location(s):** `tests/unit/agents/analysts/test_chain_callbacks.py:62-65,104-107`
- **Source-audit cross-ref:** §A.7
- **Confidence:** low
- **Description:**
  Asserts `_chain_before() is None` and `_chain_before(None, None) is None`. These are trivial — but they pin a real ADK invariant (chain collapses to `None` so ADK skips the slot). Borderline P3 cosmetic, kept at P2 because the test is short and the assertion is positive (verifies a specific output value).
- **Suggested action:**
  Leave as-is; combine into a single parametrised test if the file is touched.

### P2-06 · T8 layout · `tests/analysts/test_branch_composition.py` and `test_per_ticker_branch.py` are LLM-analyst territory, not deterministic-analyst

- **Location(s):** `tests/analysts/test_branch_composition.py`, `tests/analysts/test_per_ticker_branch.py`
- **Source-audit cross-ref:** none — these are out-of-scope for *this* test audit but in-scope for the LLM-analyst test audit
- **Confidence:** high
- **Description:**
  Both files live in `tests/analysts/` alongside `test_technical.py` and `test_smart_money.py` (deterministic-analyst smoke tests), but they exclusively exercise the **News and Fundamental** per-ticker branches — LLM-analyst territory. They share no fixtures or imports with the deterministic-analyst tests. Their presence in the same folder is a discovery-time false signal: a maintainer looking for "tests of the deterministic analysts" naturally clicks `tests/analysts/` and finds this mixed bag. Filed here so the consolidator can surface it in the cross-subsystem layout pass.
- **Suggested action:**
  Move both files to `tests/unit/agents/analysts/news/` and `tests/unit/agents/analysts/fundamental/` (or wherever the LLM-analyst test audit recommends). Surfaced here for visibility; the LLM-analyst test audit owns the final disposition.

### P2-07 · T3 weak assertion · `test_lookbacks_sourced_from_config.py` has a stale `pytest.mark.skip` on notable_holders cache-fill

- **Location(s):** `tests/contract/test_lookbacks_sourced_from_config.py:285-322`
- **Source-audit cross-ref:** none direct (peripheral to memory_smart_money_disabled note)
- **Confidence:** high
- **Description:**
  The `test_backtest_notable_holders_uses_config_lookback_and_limit` test is `@pytest.mark.skip(reason="notable_holders cache-fill is shelved ... unskip together with re-enabling the domain and the SmartMoney analyst once a subject-side notable-holders provider lands.")`. Per test-policy §T1 / RUBRIC §T1, "skipped for more than a single commit without an open ticket" is a T1 candidate. The skip cleanly references the SmartMoney Open Question #1 — if SmartMoney is deleted, this skipped test goes with it.
- **Suggested action:**
  Conditional T1: delete in the same PR as the SmartMoney deletion; alternatively, file a backlog ticket and link it from the skip reason.

### P3-01 · T8 / cosmetic · stale `Phase 5 Task 8` / `Phase 7.6 Task 17` references in test docstrings

- **Location(s):** `tests/analysts/test_technical.py:2-7`, `tests/unit/test_smart_money_fetch.py:1-15`, `tests/unit/test_social_fetch.py:1-12`, `tests/unit/test_extract_social_features.py:1-7` (and several others)
- **Confidence:** high
- **Description:**
  Many in-scope test files open with module docstrings that reference specific phase tasks ("Phase 5 Task 4", "Phase 7.6 Task 17", "Phase 7 (Task 2.11 / Fix K)", "Phase 9 post-parallelism"). These are useful while the relevant phase is fresh in memory and rapidly become test-archaeology debt. Per test-policy §D "Comments and style", function docstrings are mandatory but cosmetic phase-tracking is not. Pure P3.
- **Suggested action:**
  Lift the test intent into the function docstrings (which most already have), drop the phase-task framing from the module docstring. Pick up on any in-flight touch of each file.

### P3-02 · T8 / cosmetic · missing `@pytest.mark.integration` on `tests/integration/test_analyst_pool.py`

- **Location(s):** `tests/integration/test_analyst_pool.py`
- **Confidence:** medium
- **Description:**
  Per test-policy §C, tests in `tests/integration/` should carry `@pytest.mark.integration`. This file has none. The tests themselves are pure unit-shape (topology-only), so the right fix is to move them out of `tests/integration/` per P2-04 — but absent that, the marker is missing.
- **Suggested action:**
  Resolved by P2-04 (move to `tests/unit/`). If the file stays, add the marker.

---

**Cross-subsystem dependencies for the consolidator:**

1. The SmartMoney delete-or-fix decision (Open Question #1) gates ≈ 37 tests across 7 files. The audit's findings explicitly call out conditional dispositions.
2. P2-06 surfaces LLM-analyst test files mis-located in `tests/analysts/` — the LLM-analyst test audit needs to take ownership of the move.
3. P0-02's expansion of `test_evidence_callback.py` to parameterise across all three analysts will touch the news/fundamental joiner code paths peripherally — coordinate with LLM-analyst audit.
4. P1-03 (as_of coercion) shares a theme with the strategist and backtest audits — there's a project-wide consistency pattern to enforce, not three independent fixes.
