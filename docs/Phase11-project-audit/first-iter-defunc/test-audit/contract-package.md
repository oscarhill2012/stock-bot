# Test audit — src/contract/ (shared schemas, digest, prompt, extractors)

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/contract.md`
**Test files in scope:** 22 (full list below)
**Tests collected from those files:** 184 (via `pytest <paths> --collect-only -q`)
**Findings:** 0 P0 · 9 P1 · 8 P2 · 2 P3

## Files in scope

Tests for `src/contract/` live in five distinct trees. The layout sprawl is itself a T8 finding (P2-08).

**`tests/contract/` (root-level contract layer)** — 1 file
- `tests/contract/test_evidence_schema.py` — the D1.1 `report`-required validator

**`tests/unit/contract/` (canonical mirror)** — 9 files
- `tests/unit/contract/test_analyst_report.py`
- `tests/unit/contract/test_digest.py`
- `tests/unit/contract/test_digest_defaults.py`
- `tests/unit/contract/test_evidence.py`
- `tests/unit/contract/test_evidence_raw_text.py`
- `tests/unit/contract/test_extractor_as_of.py`
- `tests/unit/contract/test_invariants_doc_carveout.py`
- `tests/unit/contract/test_strategist_prompt_layout.py`
- `tests/unit/contract/test_ticker_evidence.py`

**`tests/unit/contract/extractors/`** — 6 files
- `tests/unit/contract/extractors/test_fundamental.py`
- `tests/unit/contract/extractors/test_news.py`
- `tests/unit/contract/extractors/test_sector_map.py`
- `tests/unit/contract/extractors/test_smart_money.py`
- `tests/unit/contract/extractors/test_social.py`
- `tests/unit/contract/extractors/test_technical.py`

**`tests/unit/`** (six root-level files that should live nearer the rest) — 6 files
- `tests/unit/test_analyst_name_literal.py`
- `tests/unit/test_decision_logger_strict_serialiser.py` (uses `AnalystVerdict` fixture only)
- `tests/unit/test_derive_smart_money_verdict.py`
- `tests/unit/test_derive_social_verdict.py`
- `tests/unit/test_derive_technical_verdict.py`
- `tests/unit/test_evidence_index.py`
- `tests/unit/test_evidence_row_persistence.py`
- `tests/unit/test_extract_fundamental_features.py`
- `tests/unit/test_extract_social_features.py`
- `tests/unit/test_strategist_prompt_risk_substitutions.py`
- `tests/unit/test_strategist_prompt_worked_examples_ticker.py`

**Fixtures** — JSON fixtures consumed by the extractor tests
- `tests/fixtures/contract/fundamental_aapl.json`
- `tests/fixtures/contract/news_aapl.json`
- `tests/fixtures/contract/smart_money_aapl.json` — note: encodes a **dead** payload shape (P1-02 below)
- `tests/fixtures/contract/smart_money_no_data.json`
- `tests/fixtures/contract/technical_aapl.json`

## Summary

The contract package's test suite is mostly well-aimed and unit-scoped — every test stubs at the function boundary, no live I/O, and the validator-driven tests (`test_evidence_schema.py`, `test_analyst_report.py`) cover D1.1 cleanly. The themes that dominate the findings: (1) **the extractor tests are the load-bearing thing keeping the source-audit's parallel raw-payload shapes alive** — the smart_money test fixture exercises the dead `"filings"` + dict-of-`filer_id` branch that would crash today if a production writer fed it; the news test fixture uses the dead `"news_items"` key; the fundamental test suite splits cleanly between Form4Bundle (live) and flat-list `insider_trades` (dead-in-prod) cases; (2) **alias-feature-key tests are pinning the wrong side of the rename** — the strategist prompt reads `headline_polarity_mean_7d` and `aggregate_score` (the aliases), but tests assert on both names symmetrically, so deleting the alias would silently break the prompt; (3) **`raw_text` and `social_volume_z` have dedicated tests for fields/keys no production path writes**. No P0: nothing is masking a current pipeline bug, but the C2/C1 dead-side tests will block the source-audit fix PRs from landing cleanly. T4 gap: the smart_money `.get()`-on-Pydantic crash path (source P1-04) has no surfacing test today.

## Findings

### P1-01 · T2 parallel-branch defender · Fundamental flat-list `insider_trades` tests anchor the dead branch

- **Locations:**
  - `tests/unit/contract/extractors/test_fundamental.py:121-273` — `test_fundamental_splits_insider_dollars_by_transaction_code`, `test_fundamental_weights_senior_officer_trades_via_flags`, `test_fundamental_emits_derivative_features`, `test_fundamental_counts_8k_items_in_30d_window` all feed `raw["insider_trades"]` / `raw["insider_derivative_trades"]` flat lists.
  - `tests/unit/test_extract_fundamental_features.py:203-221` — `test_senior_officer_aggregate_via_flat_list` (explicitly uses the flat-list path).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-01 (fundamental extractor's two raw-payload shapes; flat-list path is dead-in-production, `fetch_agent.py:177` emits Form4Bundle only).
- **Confidence:** high
- **Description:**
  These tests are the only callers of `_insider_aggregates_from_flat`, `_derivative_aggregates`, the `"insider_trades"` branch of `extract_fundamental_features`, and the per-transaction-code aggregate codepath. They're well-written tests of code production never reaches. Their existence is what makes the parallel branch survive: deleting the dead branch fails them. They are not currently masking a bug (production picks the other branch deterministically), but they are the test-side weight that keeps the source-audit C2 finding from collapsing.
- **Suggested action:**
  Delete in the same PR that collapses fundamental.py to the Form4Bundle shape (source P1-01 fix). If the migration goes the other way (fetch_agent → flat-list emission), invert the disposition: keep the flat-list tests and delete the Form4Bundle-shaped tests in `test_extract_fundamental_features.py:46-55, 123-167, 174-196, 228-247, 254-285, 292-307`.

### P1-02 · T2 parallel-branch defender · Smart-money fixture encodes the dead `"filings"` + dict-of-`filer_id` shape

- **Locations:**
  - `tests/fixtures/contract/smart_money_aapl.json:1-9` — fixture root uses `"filings"` key and dict shape `{"filer_id", "side", "amount", "filed"}`.
  - `tests/unit/contract/extractors/test_smart_money.py:30-76` — eight tests load this fixture and assert on `n_politicians`, `n_buys_30d`, `total_dollar_value_buys`, etc.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-04 (smart_money agent passes `list[PoliticianTrade]` to `raw["politician_trades"]`, not dicts to `raw["filings"]`) and P2-02 (`"filings"` / `"transactions"` fallbacks unreached by any production writer).
- **Confidence:** high
- **Description:**
  The fixture's shape — `"filings"` top-level key, dicts carrying `filer_id` / `side` / `amount` / `filed` — matches neither production writer nor the `PoliticianTrade` Pydantic schema (`amount_min_usd` / `amount_max_usd`, no `filer_id` or `amount`). Tests pass because the extractor's `.get()` chain finds the `"filings"` alias and the dict-loop happens to read the made-up keys. Once source P2-02 deletes the `"filings"` / `"transactions"` aliases, *and* source P1-04 fixes the field-name mismatch, every assertion in this file's politician section breaks. This is the canonical test-anchored-zombie.
- **Suggested action:**
  Two-step: in the same PR that lands source P1-04 (smart_money agent passes `.model_dump()` dicts), rewrite the fixture to the canonical `{"politician_trades": [PoliticianTrade.model_dump(), ...], "notable_holders": [...]}` shape with correct `amount_min_usd` / `amount_max_usd` fields; in the PR that lands source P2-02, delete the `"filings"` / `"transactions"` alias coverage entirely. The notable-holder tests at `:83-151` are fine — they already use the live shape.

### P1-03 · T2 parallel-branch defender · News fixture uses the dead `"news_items"` shape

- **Locations:**
  - `tests/fixtures/contract/news_aapl.json:1-13` — root uses `"news_items"` key with `published` / `headline` / `sentiment` fields.
  - `tests/unit/contract/extractors/test_news.py:21-75` — eight tests consume this fixture (`test_extracts_required_keys`, `test_news_count_matches_fixture`, `test_positive_share_calculated`, `test_polarity_mean`, `test_social_volume_z_passthrough`, etc.).
  - `tests/unit/contract/extractors/test_news.py:66, 74` — explicitly pass `{"news_items": []}` to `extract_news_features`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P2-03 (the `"articles"` and `"news_items"` aliases are unreached by any production writer — `fetch_agent.py:92` emits `{"news": [...]}`) and P2-05 (docstring labels production shape as a third-priority fallback).
- **Confidence:** high
- **Description:**
  The fixture and the empty-payload tests pin two of the three alias branches that the source audit flags for deletion. Source P2-03 wants `raw["articles"]` and `raw["news_items"]` removed and only `raw["news"]` kept. The tests do not exercise `raw["news"]` anywhere; they exclusively use the doomed names. The "post-fix" version of the news extractor's tests would need a rewritten fixture (`"news"` key) plus an update to every test that constructs an inline payload (`test_handles_empty_news`, `test_handles_missing_social_volume`).
- **Suggested action:**
  Rewrite `news_aapl.json` to the `{"news": [...]}` shape and the field names production emits (Finnhub-shaped); update the test cases that hand-construct payloads. The `test_news_reads_sentiment_field_not_polarity` test at `:82-100` already uses the `"articles"` alias and should be migrated to `"news"` rather than left to defend the dead alias.

### P1-04 · T2 parallel-branch defender · News `headline_polarity_mean` (primary) tests pin the side production doesn't read

- **Locations:**
  - `tests/unit/contract/extractors/test_news.py:50-57` — `test_polarity_mean` asserts on both `headline_polarity_mean` (primary) **and** `headline_polarity_mean_7d` (alias) with the same value.
  - `tests/unit/contract/extractors/test_news.py:69` — `test_handles_empty_news` asserts the primary key only.
  - `tests/unit/contract/extractors/test_news.py:100` — `test_news_reads_sentiment_field_not_polarity` asserts the primary key only.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-03 (back-compat alias feature keys; `strategist_prompt.py:276` reads `headline_polarity_mean_7d` — the alias — so the alias is load-bearing and the "primary" name is unused downstream).
- **Confidence:** high
- **Description:**
  The strategist prompt reads `headline_polarity_mean_7d`, not `headline_polarity_mean`. The source audit recommends dropping the alias and migrating the prompt to the primary key — *or* the reverse, dropping the primary and keeping the alias as the canonical name. Whichever side wins, the other side's test assertions are dead. The current test at `:55-57` symmetrically defends both; the two single-side assertions at `:69` and `:100` defend the loser side specifically (the name production does not consume).
- **Suggested action:**
  In the PR that lands the source P1-03 fix (pick one name), drop the assertions for the deleted name and keep only the survivor. If the prompt is migrated to `headline_polarity_mean`, the assertions at `:55`, `:69`, `:100` survive; if the alias `_7d` wins, line `:57` survives alone.

### P1-05 · T2 parallel-branch defender · Social `social_aggregate_score` (primary) vs `aggregate_score` (alias) symmetric assertions

- **Locations:**
  - `tests/unit/contract/extractors/test_social.py:28` — asserts on primary `social_aggregate_score`.
  - `tests/unit/contract/extractors/test_social.py:63-74` — `test_social_aggregate_score_back_compat_alias` asserts both keys carry the same value.
  - `tests/unit/test_extract_social_features.py:17-21` — `test_extractor_emits_expected_keys` requires both keys to be present.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-03 (`strategist_prompt.py:294` reads `aggregate_score` — the alias — so the alias is load-bearing and `social_aggregate_score` is the unused primary).
- **Confidence:** high
- **Description:**
  Mirror of P1-04 on the social side. The strategist consumes `aggregate_score`; the test in `test_social.py:28` asserts the *unused* primary key. Whichever name source P1-03 picks, the other side's tests die with it. Once the source fix lands, leaving these as-is would make the deletion PR carry stale assertions that exercise non-existent keys.
- **Suggested action:**
  Same pattern as P1-04 — drop the loser-side assertion in the source-fix PR.

### P1-06 · T2 parallel-branch defender · Technical extractor's three `_resolve_bars` shapes — tests defend all three

- **Locations:**
  - `tests/unit/contract/extractors/test_technical.py:58-70` — `test_handles_short_history_gracefully` uses `raw["price_history"]` as a flat list (the "very old legacy" shape from source P1-02 branch 3).
  - `tests/unit/contract/extractors/test_technical.py:77-164, 205-352` — all the post-Phase-7 tests use `raw["bars"]` (branch 1 — also dead in production per source P1-02).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-02 (the only live branch is branch 2 — `raw["price_history"]["bars"]`; branches 1 and 3 are unreached by `fetch.py:82-85`).
- **Confidence:** high
- **Description:**
  The technical extractor's `_resolve_bars` checks three locations. The production writer emits branch 2 (`{"price_history": ph.model_dump()}` where `ph_payload["bars"]` is the list). Every post-Phase-7 test feeds `{"bars": [...]}` at the top level — branch 1, dead. One legacy test feeds `{"price_history": [...]}` as a flat list — branch 3, also dead. **Zero tests defend the live production shape** (branch 2). That's the inverse of the usual T2: deleting the dead branches would not kill any test directly, but it leaves the *live* path uncovered. Once source P1-02 collapses to the single live branch, the test suite needs a rewrite to feed `{"price_history": {"bars": [...]}}` shape — and the absence of that test today is also a T4 gap (filed below at P1-09).
- **Suggested action:**
  In the same PR that collapses `_resolve_bars` (source P1-02 fix): rewrite all `_make_bars`-based tests to wrap the bar list inside `{"price_history": {"bars": <bars>}}`, drop the legacy flat-list test at `:58-70`, and assert at least once on the exact live shape (covers the P1-09 gap below).

### P1-07 · T1 dead test · `test_evidence_raw_text.py` exercises a never-written field

- **Location:** `tests/unit/contract/test_evidence_raw_text.py` (entire file — single test, 44 lines).
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P2-01 (`AnalystEvidence.raw_text` declared, never written or read in `src/`).
- **Confidence:** high
- **Description:**
  The test verifies that `AnalystEvidence(raw_text="...")` round-trips through `model_dump`/`model_validate`. The field has no production writer (only `raw_text=None` explicit-nulls in test code) and no production consumer. The source audit recommends deleting the field. When that lands, this entire file goes with it.
- **Suggested action:**
  Delete this file in the PR that drops `AnalystEvidence.raw_text`. Also drop the explicit `raw_text=None` line at `tests/unit/agents/strategist/test_evidence_view_missing_report.py:48` (it's no-op once the field is gone but will fail validation if the field is deleted).

### P1-08 · T1 dead test · `test_social_volume_z_passthrough` and `test_handles_missing_social_volume`

- **Locations:**
  - `tests/unit/contract/extractors/test_news.py:60-62` — `test_social_volume_z_passthrough`.
  - `tests/unit/contract/extractors/test_news.py:72-75` — `test_handles_missing_social_volume`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P2-04 (`social_volume_z` flows nowhere from production; no news provider writes the key; strategist always sees the bullet at `0.0`).
- **Confidence:** high
- **Description:**
  `social_volume_z` exists in `_KEYS`, the news extractor passes it through, and the strategist prompt has a "Social volume z:" bullet — but the feature is permanently `0.0` in production because no news provider populates it. The fixture seeds `"social_volume_z": 1.4` by hand to keep the passthrough test passing. Source P2-04 recommends deletion. When it lands, these two tests and the fixture-seeded value go with it.
- **Suggested action:**
  Delete both tests and the `"social_volume_z": 1.4` line from `news_aapl.json` in the source P2-04 fix PR.

### P1-09 · T4 missing surfacing test · No test exercises smart_money `.get()`-on-Pydantic-instance crash path

- **Location:** new test needed; closest existing files are `tests/unit/contract/extractors/test_smart_money.py` and `tests/unit/test_derive_smart_money_verdict.py`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-04 (smart_money agent passes `list[PoliticianTrade]` Pydantic instances into `raw["politician_trades"]`; the extractor calls `.get("filer_id")` on each one, which `BaseModel` doesn't implement — `AttributeError`; *and* the field names `filer_id` / `amount` don't match `PoliticianTrade`).
- **Confidence:** high
- **Description:**
  The bug is hidden today because `politician_trades` is intentionally disabled (`feedback_politician_trades_disabled` memory), so `ticker_raw.politicians` is always `[]`. The minute the provider is re-enabled, the first politician-bearing ticker raises `AttributeError` deep inside the extractor's `for f in filings:` loop — or, if the agent's flow accidentally suppresses it, silently returns zero features (which would be even worse, since it'd present as no signal). No current test feeds a list of `PoliticianTrade` Pydantic instances to `extract_smart_money_features`; all existing tests use the dict shape. The test that would catch this is exactly the test that would be required by the source P1-04 fix.
- **Suggested action:**
  Add a new test in `tests/unit/contract/extractors/test_smart_money.py` named `test_smart_money_accepts_politician_trade_pydantic_instances` that builds a `PoliticianTrade(ticker=..., politician=..., side="buy", amount_min_usd=50_000, amount_max_usd=100_000, filed_at=...)`, passes `{"politician_trades": [pt.model_dump()]}` (matching the agent's intended post-fix shape), and asserts `f["n_politicians"] == 1.0`, `f["total_dollar_value_buys"] > 0`, `f["is_no_data"] == 0.0`. Pair with a regression test feeding raw Pydantic instances (not dicts) to confirm the extractor either accepts both or raises with a clear message — not silently returns zeros.

### P2-01 · T2 alias-coverage test · `test_social_aggregate_score_back_compat_alias` documents the alias but pins its purpose backwards

- **Location:** `tests/unit/contract/extractors/test_social.py:63-74`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P1-03.
- **Confidence:** medium
- **Description:**
  The test only asserts both keys carry the *same value*; it does not assert which one downstream consumers actually read. Reading the test alone, a reader cannot tell which name is the source of truth. The strategist prompt's load-bearing read is the alias (`aggregate_score`), so the "back-compat alias" framing in the test docstring is actively misleading — the comment in source labels `social_aggregate_score` as the primary but the consumer disagrees. Filed as P2 (not P1) because the test does still pin the equality invariant, just under a misnomer.
- **Suggested action:**
  When source P1-03 picks a name, rewrite the docstring and test name to match the chosen reality. If both keys survive intentionally, rename the test to make the live consumer explicit (e.g. `test_social_score_emitted_under_both_legacy_and_canonical_names`).

### P2-02 · T3 weak assertion · `test_extracts_required_keys` only proves shape, never content

- **Locations:**
  - `tests/unit/contract/extractors/test_fundamental.py:44-47`
  - `tests/unit/contract/extractors/test_news.py:26-29`
  - `tests/unit/contract/extractors/test_smart_money.py:30-33`
  - `tests/unit/contract/extractors/test_technical.py:21-24`
- **Source-audit cross-ref:** none directly — generic test-policy §A.7 / §E concern.
- **Confidence:** medium
- **Description:**
  Each of the four extractor test files has a `test_extracts_required_keys(aapl_data)` test that asserts `set(features.keys()) == set(_KEYS)`. That tells you the extractor returned a dict with the right column names. It does not tell you that any column carries a non-zero or non-`0.0` value, and the `_zero_features()` fallback path returns the full key set with every value `0.0`. A regression that silently makes the extractor return its zero-fallback for the AAPL fixture would still pass this test. The follow-up tests (`test_all_features_are_floats`, `test_pe_values_carried_through`, etc.) partially compensate, but the key-set test by itself is decorative.
- **Suggested action:**
  Either delete the four `test_extracts_required_keys` tests (covered by content tests anyway) or pair each with a `assert sum(abs(v) for v in features.values()) > 0` floor — "the fixture exercised at least one populated column".

### P2-03 · T3 weak assertion · `test_all_features_are_floats` is shape-only

- **Locations:** same four files as P2-02 above (`test_all_features_are_floats`).
- **Confidence:** low
- **Description:**
  Asserts every value is a `float`. Doesn't assert the values are non-zero. Already covered by `set(_KEYS)` + the schema-level cast in `_zero_features`. A regression that returns all-zeros still passes.
- **Suggested action:**
  Strengthen by adding at least one content assertion per file, or fold into the corresponding content test.

### P2-04 · T2 parallel-branch defender · `as_of` parametrised test pins the "every extractor accepts as_of" surface for both live and dead extractor shapes

- **Location:** `tests/unit/contract/test_extractor_as_of.py:32-46` — `test_clock_free_extractors_accept_as_of` parametrised over `contract.extractors.technical`, `contract.extractors.news`, `contract.extractors.smart_money`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/contract.md` P2-06 (technical `state` docstring stale — `as_of` is now wired via state["reference_prices"]; the parameter is "accepted" but the docstring is wrong).
- **Confidence:** low
- **Description:**
  Cosmetic test that checks the `as_of` parameter is in the signature via `inspect.signature`. Doesn't crash with the source-audit findings, but pins the parameter contract symmetrically across extractors. It's worth keeping as a uniformity guard.
- **Suggested action:**
  Leave as-is. Filed as P2 only because the docstring-driven `test_clock_free_extractors_accept_as_of` shape (signature introspection without execution) is a soft form of T3 — passes even if the parameter is silently ignored.

### P2-05 · T8 layout · Six tests of `src/contract/` symbols live in `tests/unit/` root instead of `tests/unit/contract/`

- **Locations:**
  - `tests/unit/test_analyst_name_literal.py` → should be `tests/unit/contract/test_analyst_name_literal.py`
  - `tests/unit/test_extract_fundamental_features.py` → should be `tests/unit/contract/extractors/test_fundamental_extra.py` (or merged into the existing one)
  - `tests/unit/test_extract_social_features.py` → should be `tests/unit/contract/extractors/test_social_extra.py` (or merged)
  - `tests/unit/test_derive_smart_money_verdict.py` → testing `contract.extractors.smart_money` — belongs near it
  - `tests/unit/test_derive_social_verdict.py` → ditto for social
  - `tests/unit/test_derive_technical_verdict.py` → ditto for technical
  - `tests/unit/test_strategist_prompt_risk_substitutions.py` → testing the rendered prompt that lives in `contract.strategist_prompt`, but uses the *strategist* agent's prompts module; arguably belongs under `tests/unit/agents/strategist/`
  - `tests/unit/test_strategist_prompt_worked_examples_ticker.py` → same as above
- **Source-audit cross-ref:** none — pure layout finding per test-policy §B.
- **Confidence:** high
- **Description:**
  The policy expects unit tests to mirror the source tree. Half the contract-package tests do; the other half live in `tests/unit/` flat. The two flat-tree extractor test files (`test_extract_fundamental_features.py`, `test_extract_social_features.py`) overlap meaningfully with `tests/unit/contract/extractors/test_{fundamental,social}.py` — the duplication is the real problem because identical-named fixtures (`_h()`, `_features()`) are re-implemented per file.
- **Suggested action:**
  Move + consolidate: merge `test_extract_fundamental_features.py` into `tests/unit/contract/extractors/test_fundamental.py` (or rename and keep separated by Phase intent), and likewise for the social file. Move the `test_derive_*` trio into `tests/unit/contract/extractors/` as `test_<extractor>_verdict.py`. Move `test_analyst_name_literal.py` into `tests/unit/contract/`. The two strategist-prompt tests are borderline — they exercise `agents.strategist.prompts` not `contract.strategist_prompt`, so they belong under `tests/unit/agents/strategist/`.

### P2-06 · T1 dead test · `test_invariants_doc_carveout.py` couples test to a doc file that lives outside contract source

- **Location:** `tests/unit/contract/test_invariants_doc_carveout.py:14-34`.
- **Source-audit cross-ref:** none — but lives in the contract-tests tree while testing docs.
- **Confidence:** medium
- **Description:**
  This test asserts presence of substrings in `docs/contract-invariants.md` and `docs/Phase8-contract-audit-fixes/contract-audit.md`. It's defending an A2.4 carve-out clause whose lifecycle is the doc, not the code. If the doc is restructured or the Phase 8 audit file is archived, this test breaks for non-code reasons. It also doesn't actually verify the carve-out *behaves* correctly — only that the prose mentions it.
- **Suggested action:**
  Delete and replace with a behavioural test of the in-tick callback path in `tests/unit/agents/strategist/` — or, if the doc-pin is genuinely the desired contract, move to a `tests/docs/` tree with a clear "this is a doc-pin" marker. Not blocking any source fix, so disposition is hygiene.

### P2-07 · T3 weak assertion · Strategist prompt layout tests use `"X" in out` substring checks that don't pin location

- **Locations:** `tests/unit/contract/test_strategist_prompt_layout.py:302-512` — most tests use bare `assert "76" in out`, `assert "12.3" in out`, `assert "84.2" in out`, etc.
- **Confidence:** medium
- **Description:**
  The tests check that an expected numeric appears anywhere in the rendered string. They don't check it appears in the right section — a regression that swaps the RSI value into the Fundamental block, or that drops the Technical section entirely while leaking "76" somewhere else, still passes. The `test_no_report_omits_drivers_block` test at `:456-478` is the only one that slices by section header before asserting. Filed P2 not P1 because the file as a whole is thorough — but the rendering correctness it claims to verify is looser than the docstrings imply.
- **Suggested action:**
  Slice by section header (`[Technical]` … `[Fundamental]`) before asserting on individual values, the way `test_no_report_omits_drivers_block` already does. Pair the per-feature assertions with an "in the right section" guarantee.

### P2-08 · T8 layout · Contract tests live in five different directories

- **Location:** see "Files in scope" above.
- **Confidence:** high
- **Description:**
  `tests/contract/test_evidence_schema.py` (validators) + `tests/unit/contract/` (mostly canonical) + `tests/unit/contract/extractors/` (extractors only) + the six `tests/unit/` flat files (P2-05). Test-policy §B says contract-layer tests of *layer-boundary invariants* (schemas, signatures) belong in `tests/contract/`; per-function unit tests belong in `tests/unit/<mirror>/`. The split is reasonable but not consistently applied — `tests/unit/contract/test_analyst_report.py` and `tests/unit/contract/test_evidence.py` arguably belong in `tests/contract/` alongside `test_evidence_schema.py`, since they're schema-rule tests not function-input tests.
- **Suggested action:**
  Either move all three schema-rule files (`test_evidence.py`, `test_analyst_report.py`, `test_ticker_evidence.py`, `test_evidence_schema.py`) into `tests/contract/` and tag with the `contract` marker, or rename `tests/contract/` to clarify its narrower scope. Resolve the P2-05 stragglers in the same pass.

### P3-01 · T3 cosmetic · `test_substitutions_track_config_changes` mutates a module global via `monkeypatch.setattr`

- **Location:** `tests/unit/test_strategist_prompt_risk_substitutions.py:31-61`.
- **Confidence:** low
- **Description:**
  The test does `monkeypatch.setattr(rg, "_DEFAULT_PATH", cfg_file)` and `rg.get_risk_gate_config.cache_clear()` then reloads `agents.strategist.prompts`. Cleanup is handled by `monkeypatch` automatically, but the cache-clear pattern means other tests that ran first against the cached default value could observe a stale prompt. Minor — the reload is correct here.
- **Suggested action:**
  Leave as-is unless the reload-based pattern starts colliding with other tests. Worth noting as a known soft-state mutation.

### P3-02 · T3 cosmetic · `test_invariants_carveout_clause_present` walks the project tree by relative parent-count

- **Location:** `tests/unit/contract/test_invariants_doc_carveout.py:14`.
- **Confidence:** low
- **Description:**
  `_PROJECT_ROOT = Path(__file__).resolve().parents[3]` — fragile to file relocation. If P2-05 / P2-06 above move the file, the test breaks for unrelated reasons.
- **Suggested action:**
  Use `pytest` `rootdir` (`pytestconfig.rootpath` via fixture) instead of hand-walking `parents`. Subsumed by the P2-06 deletion if that lands.

---

## Notes for the consolidator

- **PR-coupling matrix.** Several P1 findings collapse cleanly into the source-audit PR that lands their cross-referenced fix:
  - Source P1-01 (fundamental flat-list deletion) → test P1-01 deletion.
  - Source P1-02 (technical `_resolve_bars` collapse) → test P1-06 (rewrite to live shape) + closes the T4 gap implied by it.
  - Source P1-03 (alias key pick) → test P1-04 + P1-05 + P2-01 (drop loser-side assertions).
  - Source P1-04 (smart_money `.get()` on Pydantic) → test P1-02 (fixture rewrite) + P1-09 (new T4 test).
  - Source P2-01 (`raw_text` deletion) → test P1-07 (file deletion).
  - Source P2-02 (smart_money `"filings"`/`"transactions"` alias deletion) → test P1-02 second half.
  - Source P2-03 (news `"articles"`/`"news_items"` alias deletion) → test P1-03 (fixture + inline-payload rewrites).
  - Source P2-04 (`social_volume_z` deletion) → test P1-08.
- **Cross-subsystem dependency.** Confirming source P1-04 (smart_money agent dict-conversion) requires the analyst-agents audit to confirm the agent's pass-through path (`src/agents/analysts/smart_money/agent.py:142-145`). Test P1-09 here can be written in advance against the **intended** post-fix contract (`.model_dump()` dicts) — failing today documents the gap correctly.
- **No P0 findings.** Nothing in the contract test suite is masking a current live bug. The biggest risk band is "the source fixes will be blocked by stale tests" — exactly the C2 collapse that P1-01 through P1-06 address.
- **184 tests collected** across 22 files; recommendation summary:
  - Delete outright: 2 files (P1-07 `test_evidence_raw_text.py`, P2-06 `test_invariants_doc_carveout.py`) + 2 isolated tests (P1-08).
  - Delete conditional on source PR: ~15 tests within `test_fundamental.py`, `test_smart_money.py`, `test_news.py`, `test_social.py`.
  - Strengthen in place: ~10 tests (P2-02, P2-03, P2-07).
  - Move/consolidate: 6–8 files (P2-05, P2-08).
  - Add new: 1 test (P1-09).
