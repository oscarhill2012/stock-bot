# Test audit — `src/agents/analysts/fundamental/` and `src/agents/analysts/news/`

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` (primary); `docs/test-policy.md` §A, §E, §G.4
**Test files in scope:** 15 (full list below)
**Tests collected from those files:** 77 (via `pytest <paths> --collect-only -q`)
**Findings:** 2 P0 · 4 P1 · 6 P2 · 2 P3

## Files in scope

Tests covering the two LLM analyst sub-trees live in **three** parallel directories — see P1-04 (T8 layout):

- `tests/analysts/fundamental/` — 3 files
  - `tests/analysts/fundamental/test_fetch_agent.py`
  - `tests/analysts/fundamental/test_joiner.py`
  - `tests/analysts/fundamental/test_prompts.py`
- `tests/analysts/news/` — 3 files
  - `tests/analysts/news/test_fetch_agent.py`
  - `tests/analysts/news/test_joiner.py`
  - `tests/analysts/news/test_prompts.py`
- `tests/analysts/` (shared across both, plus other analysts) — 2 files
  - `tests/analysts/test_branch_composition.py`
  - `tests/analysts/test_per_ticker_branch.py`
- `tests/unit/` (root-level, single-purpose prompt tests) — 7 files
  - `tests/unit/test_fundamental_prompt_render.py`
  - `tests/unit/test_fundamental_prompt_decision_rule.py`
  - `tests/unit/test_fundamental_prompt_report_required.py`
  - `tests/unit/test_news_prompt_render.py`
  - `tests/unit/test_news_prompt_bearish_nudge.py`
  - `tests/unit/test_news_prompt_report_required.py`
  - `tests/unit/test_analyst_prompts_anti_truncation.py`
- `tests/unit/agents/analysts/` (one file in a properly-mirrored location) — 1 file
  - `tests/unit/agents/analysts/test_report_cache_version.py`
- `tests/agents/` — 1 file
  - `tests/agents/test_output_caps_per_ticker.py`
- `tests/integration/` — 2 files (only one is in-scope analyst-specific; the other touches contract-schema)
  - `tests/integration/test_fundamental_canned_output.py` (in scope — Fundamental schema)
  - `tests/integration/test_analyst_pool.py` (in scope — structural wiring)

**Out of scope (reference-only):** `tests/unit/contract/extractors/test_fundamental.py`, `tests/unit/contract/extractors/test_news.py`, `tests/unit/test_extract_fundamental_features.py`, `tests/unit/data/providers/test_news_*.py`, `tests/unit/data/models/test_news.py` — these test the extractor and provider seams that feed *into* the LLM analyst joiner/fetcher; they are owned by other audits.

## Summary

The suite covers shape and wiring well — branch composition, per-ticker LlmAgent wrappers, output-cap propagation, prompt rendering, schema validation — but is **structurally blind to the silent-fetch-failure attractor** the source audit identifies as P1-01 / P1-02. Both `test_fetch_degrades_on_provider_error` tests deliberately *codify* the swallow-and-continue behaviour as correct (asserting empty payloads land, no warning surfaces upstream), so the very fix the source audit recommends would break these tests — they actively defend the attractor. Secondary themes: a major T8 layout problem (analyst tests live across four different roots — `tests/analysts/`, `tests/unit/`, `tests/unit/agents/analysts/`, `tests/agents/`); the §A doc-gap for `*_evidence` keys means no test asserts on `feature_warnings` content; and one P3-02 silent ticker-override is undefended. No live parallel-old-new tests (T2) survive — the retired `build_news_analyst` / `build_fundamental_analyst` factories are fully gone from the test suite.

## Findings

### Fundamental

### P0-01 · T4 missing surfacing test · No test asserts a fundamental provider raise surfaces as `feature_warning` rather than as silent `is_no_data`

- **Location(s):** new test needed (no existing test covers this); the closest existing test `tests/analysts/fundamental/test_fetch_agent.py::test_fetch_degrades_on_provider_error` actively codifies the *opposite* behaviour as correct
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P1-01 (three sequential `try / except Exception:` blocks at `src/agents/analysts/fundamental/fetch_agent.py:118-163`)
- **Confidence:** high
- **Description:**
  The fundamental fetch agent swallows three independent provider exceptions per ticker (`get_company_ratios`, `get_company_filings`, `get_insider_trades`) into `None` / `[]` / empty `Form4Bundle` placeholders. The downstream LLM is still invoked with a near-empty context block and is expected to self-declare `is_no_data=true` — a fragile path that nothing structural defends. No test in scope asserts that a provider raise produces (a) a structured `feature_warning` on the eventual `AnalystEvidence`, (b) a `_LOGGER.warning` line caplog can verify, or (c) a synthetic no-data verdict short-circuit when *all three* providers fail. The existing `test_fetch_degrades_on_provider_error` (`test_fetch_agent.py:157-216`) asserts that MSFT's `ratios is None`, `filings == []`, and the per-ticker context still exists — i.e. it *requires* the swallow-and-continue with no upstream signal. This test will need to be replaced when the source P1-01 lands.
- **Suggested action:**
  Add a new test `test_fetch_provider_raise_surfaces_as_feature_warning` (parallel pair on each side) that uses `caplog.set_level(WARNING)`, raises from the stubbed leaf fetch, asserts the warning is recorded *and* that a structured warning is propagated through state so the joiner can lift it onto `AnalystEvidence.feature_warnings`. Concurrently, contingent on source P1-01 landing, delete or rewrite `test_fetch_degrades_on_provider_error` — it currently defends the attractor.

### P1-01 · T3 · Joiner happy-path test only counts verdicts, never asserts content

- **Location:** `tests/analysts/fundamental/test_joiner.py:67-124` (`test_joiner_builds_canonical_keys_from_per_ticker_state`)
- **Source-audit cross-ref:** general test-policy §A.7 / §E "Asserting only on counts, never on content"
- **Confidence:** high
- **Description:**
  The happy-path joiner test asserts `verdict_tickers == {"AAPL", "MSFT"}` and `ev_tickers == {"AAPL", "MSFT"}`. Both LLM verdicts in the fixture state are `lean: "bullish"`, `confidence: 0.8` / `0.6`, `is_no_data: False` — but the test never asserts that those values survive the joiner round-trip. A regression where the joiner accidentally overwrites every verdict with the no-data synthetic (per `joiner.py:67-76`) would still pass: both tickers would still be present, both rows would still appear in `news_evidence`. Per §G.7 ("`is_no_data=True` is a silent-failure attractor"), assert `is_no_data=False` on the happy-path explicitly so this trap fails the test.
- **Suggested action:**
  Add per-ticker content assertions: `assert verdicts[0].lean == "bullish"`, `assert not verdicts[0].is_no_data`, `assert verdicts[0].confidence == 0.8`. Same shape across the news mirror (P1-02 below). Two lines per test, large defensive value.

### P2-01 · T8 layout · Fundamental prompt tests split across two roots with different naming

- **Locations:** `tests/analysts/fundamental/test_prompts.py` (in the `tests/analysts/` tree); `tests/unit/test_fundamental_prompt_render.py`, `tests/unit/test_fundamental_prompt_decision_rule.py`, `tests/unit/test_fundamental_prompt_report_required.py` (in the `tests/unit/` root, flat)
- **Source-audit cross-ref:** test-policy §B (Unit tests live under `tests/unit/` mirroring the source tree)
- **Confidence:** high
- **Description:**
  Per test-policy §B, the canonical Unit location is `tests/unit/<module-mirror>/` — for `src/agents/analysts/fundamental/prompts.py` that is `tests/unit/agents/analysts/fundamental/test_prompts.py`. Today the same prompt is tested by two co-existing files in two different directories using two different vocabulary helpers — `test_prompts.py` and `test_fundamental_prompt_render.py` both call `build_fundamental_instruction(_vocab())` with different vocabularies and assert overlapping things. Consolidation can land alongside the §B layout migration without changing behaviour.
- **Suggested action:**
  Consolidate all fundamental-prompt tests into `tests/unit/agents/analysts/fundamental/test_prompts.py`. Pull the four `tests/unit/test_fundamental_prompt_*.py` files into thematic sections in the canonical location; share one `_vocab()` helper through a local conftest. Same operation mirrors for news prompts (see P2-02).

### P2-03 · T3 · Branch-composition tests confirm shape but no behavioural assertion

- **Location:** `tests/analysts/test_branch_composition.py` (entire file)
- **Source-audit cross-ref:** general test-policy §A.7
- **Confidence:** medium
- **Description:**
  All three branch-composition tests only check `isinstance` and `len(sub_agents) == 3` and `inner.analyst == "fundamental"`. No assertion that the branch actually does anything — e.g. that constructing then driving the branch through one tick produces a non-empty `fundamental_verdicts` key, or that the joiner is wired downstream of the parallel fan-out (the order of `sub_agents[0]` vs `sub_agents[-1]` is asserted via `isinstance(subs[-1], FundamentalJoinerAgent)` which catches reorder but not, e.g., a future Joiner that silently writes nothing). Acceptable as a wiring smoke test, but it's a candidate for strengthening — a single end-to-end stubbed-LLM "branch produces verdicts" test would add real coverage for the wiring assertion these structural tests imply.
- **Suggested action:**
  Either keep as-is (acknowledged shape-only) or extend with one driven test that runs the branch end-to-end with a stubbed LLM and asserts non-empty `fundamental_verdicts` in the resulting state delta. Marginal — only worth doing if a similar driven test already exists for news (see P2-04).

### P2-05 · T4 missing surfacing test · No test asserts §A `fundamental_evidence` row exists and carries the expected `feature_warnings` field

- **Location:** new test needed; closest existing assertion is `tests/analysts/fundamental/test_joiner.py:122-124` which asserts only `ev_tickers == {"AAPL", "MSFT"}`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P2-04 (no §A row for `news_evidence` / `fundamental_evidence`)
- **Confidence:** medium
- **Description:**
  The source audit P2-04 calls out that `news_evidence` and `fundamental_evidence` are written to state every tick but have no §A row in `docs/contract-invariants.md`. The test layer reflects that gap: no test in scope asserts on the *shape* of the `fundamental_evidence` row beyond ticker membership. In particular, the `feature_warnings` field on `AnalystEvidence` — which the strategist's `context_shim._index_evidence` reads, and which is the natural place to land the fundamental P1-01 surfacing fix — is never asserted on. Add a test that pins the row contains `feature_warnings` (empty on happy path) so the §A row addition has a test counterpart on day one.
- **Suggested action:**
  Add `test_joiner_evidence_row_carries_feature_warnings_field` asserting `assert "feature_warnings" in delta["fundamental_evidence"][0]` and `assert delta["fundamental_evidence"][0]["feature_warnings"] == []` on the happy path; mirror on news. This wires the test for the P0-01 surfacing fix.

### P3-01 · T8 minor · Single-file naming inconsistency between fundamental joiner test and fetch-agent test

- **Location:** `tests/analysts/fundamental/test_fetch_agent.py` vs `tests/analysts/fundamental/test_joiner.py`
- **Source-audit cross-ref:** test-policy §D Naming
- **Confidence:** low
- **Description:**
  Per §D, files are `test_<thing>_<aspect>.py`. The fetch-agent file is `test_fetch_agent.py` (the *thing*), but the joiner is `test_joiner.py`. Either both should use the agent suffix (`test_joiner_agent.py`) or both should drop it (`test_fetch.py`). Cosmetic; rename when consolidating P2-01.
- **Suggested action:**
  Cosmetic — pick one convention on the consolidation pass.

### News

### P0-02 · T4 missing surfacing test · No test asserts a news provider raise surfaces as `feature_warning` rather than as silent empty articles

- **Location(s):** new test needed; closest existing test `tests/analysts/news/test_fetch_agent.py::test_fetch_degrades_on_provider_error` actively codifies the *opposite* behaviour as correct (asserts `articles == []` and `(no news available)` placeholder land with no upstream signal)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P1-02 (`try / except Exception:` around `get_stock_news` at `src/agents/analysts/news/fetch_agent.py:80-86`)
- **Confidence:** high
- **Description:**
  Identical shape to P0-01 on the fundamental side. The news fetch agent collapses a failed `get_stock_news` to `articles = []`, the per-ticker context becomes `(no news available)`, the LLM is still invoked, and `(no news available)` from a fetch failure is indistinguishable from `(no news available)` from a genuinely empty news window. No test in scope asserts the warning fires; no test distinguishes degraded from empty. Source audit P1-02 names this as the canonical T4 gap. The existing `test_fetch_degrades_on_provider_error` (`test_fetch_agent.py:73-103`) asserts the swallow-and-continue is the intended behaviour — it will break under the source fix. Note also test-policy §E specifically calls out "Stubbing the wrong news provider" (`config/data.json` currently routes to `finnhub`); these tests do stub the right seam (`agents.analysts.news.fetch_agent.get_stock_news` — the analyst-import binding), but the underlying point still applies: the test confirms swallowing happens silently, not that the failure surfaces.
- **Suggested action:**
  Add `test_fetch_provider_raise_surfaces_as_feature_warning_news` (parallel to fundamental P0-01). Use `caplog.set_level(WARNING)`, raise `RuntimeError("provider down")` from the stubbed `get_stock_news`, assert (i) the warning is logged with the ticker name, and (ii) a structured warning is propagated through state so the joiner can lift it onto `AnalystEvidence.feature_warnings`. Replace or rewrite the existing `test_fetch_degrades_on_provider_error` once source P1-02 lands — currently it codifies the attractor.

### P1-02 · T3 · News joiner happy-path test only counts verdicts, never asserts content

- **Location:** `tests/analysts/news/test_joiner.py:16-64` (`test_joiner_builds_canonical_keys_from_per_ticker_state`)
- **Source-audit cross-ref:** general test-policy §A.7 / §E "Asserting only on counts, never on content"; §G.7 (`is_no_data=True` attractor)
- **Confidence:** high
- **Description:**
  Mirror of P1-01 above. `verdict_tickers == {"AAPL", "MSFT"}` is the only content assertion on the happy path. The fixture state has `lean: "bullish"` / `confidence: 0.8` / `is_no_data: False` on both verdicts, none of which is asserted to survive joining. A regression where the joiner overwrites every verdict with the no-data synthetic at `joiner.py:67-76` would still pass.
- **Suggested action:**
  Add `assert v.lean == "bullish"` / `assert not v.is_no_data` / `assert v.confidence == 0.8` per ticker. Same minimal patch as P1-01.

### P1-03 · T2/T3 · `test_fetch_degrades_on_provider_error` (both sides) codifies the silent-failure attractor as correct

- **Locations:** `tests/analysts/news/test_fetch_agent.py:73-103`, `tests/analysts/fundamental/test_fetch_agent.py:157-216`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P1-01, P1-02 (silent-failure attractors)
- **Confidence:** high
- **Description:**
  These two tests are *contingent dead-on-arrival* once source P1-01/P1-02 land. They assert: (a) provider raise → `articles == []` / `ratios is None`, (b) per-ticker context still exists with placeholder text, (c) no upstream signal of failure. The source fix is to *change* (c): a raise must surface as a structured warning. The fundamental version of this test is more egregious (it explicitly tolerates three independent failure paths swallowing into placeholders). Once the surfacing fix lands, both tests are wrong — they will assert the absence of a warning the new code will emit. Flag them now for the same PR that lands source P1-01/P1-02.
- **Suggested action:**
  Mark as "rewrite, do not delete" — the *intent* (degraded ticker doesn't break sibling tickers) is good; the *assertion* (no upstream signal) is wrong post-fix. After the surfacing fix lands, change to: provider raise on MSFT → MSFT's verdict appears in the verdict list with `is_no_data=True` *and* a `feature_warnings` entry naming the failed seam; AAPL's verdict is unaffected.

### P2-02 · T8 layout · News prompt tests split across two roots with different naming (mirror of P2-01)

- **Locations:** `tests/analysts/news/test_prompts.py`; `tests/unit/test_news_prompt_render.py`, `tests/unit/test_news_prompt_bearish_nudge.py`, `tests/unit/test_news_prompt_report_required.py`
- **Source-audit cross-ref:** test-policy §B
- **Confidence:** high
- **Description:**
  Identical to P2-01 on the news side. Per §B, all news-prompt unit tests belong under `tests/unit/agents/analysts/news/test_prompts.py`. Today the same prompt is tested by `tests/analysts/news/test_prompts.py` *and* three flat-root `tests/unit/test_news_prompt_*.py` files. Each uses its own `_vocab()` helper with slightly different vocabularies, which is also a duplication-cost issue.
- **Suggested action:**
  Consolidate to `tests/unit/agents/analysts/news/test_prompts.py`. Pull all four files together; share one vocab helper via a local conftest.

### P2-04 · T4 missing surfacing test · No test asserts §A `news_evidence` row carries the `feature_warnings` field (mirror of P2-05)

- **Location:** new test needed; closest existing assertion is `tests/analysts/news/test_joiner.py:61-64` which asserts only `ev_tickers == {"AAPL", "MSFT"}`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P2-04
- **Confidence:** medium
- **Description:**
  Mirror of P2-05 above. The `news_evidence` row is never asserted to contain a `feature_warnings` field on happy-path or degraded paths. Add a positive-content test to lock in the shape so the §A contract row and the eventual surfacing fix have a test counterpart.
- **Suggested action:**
  As P2-05 — add `test_joiner_evidence_row_carries_feature_warnings_field` on the news joiner side.

### P2-06 · T3 · `test_joiner_synthesises_no_data_for_missing_key` doesn't distinguish "branch failed silently" from "LLM emitted no-data"

- **Locations:** `tests/analysts/news/test_joiner.py:67-108`, `tests/analysts/fundamental/test_joiner.py:127-173`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P3-02 (silent ticker override); test-policy §G.8 (`branch_failed` warnings are not benign)
- **Confidence:** medium
- **Description:**
  Both tests simulate a missing `temp:<analyst>_verdict_<TICKER>` key and assert the joiner synthesises `is_no_data=True`. The behaviour is correct — but the joiner cannot distinguish "the LlmAgent ran and chose to omit output" from "the LlmAgent never ran because the wrapper aborted with `branch_failed`". The test asserts `is_no_data is True` without any caplog assertion that this synthesis path was reached intentionally vs. by silent branch death. Per §G.8, pipeline-level tests should `caplog.set_level(WARNING)` and verify whether `branch_failed` was logged. These joiner-level tests aren't pipeline-level, but they could at least assert a log line ("synthesised no-data verdict for MSFT — verdict key absent") so a downstream consumer can monitor the synthesis rate.
- **Suggested action:**
  Add a caplog assertion that synthesis emits a structured log line so the rate of synthetic no-data verdicts is observable. Currently the joiner emits nothing on this path (`joiner.py:66-76`) — fix is half source-side (emit a log) and half test-side (assert it fires).

### P3-02 · T4 missing test · Joiner's silent ticker-field override is undefended

- **Locations:** new test needed for both `tests/analysts/news/test_joiner.py` and `tests/analysts/fundamental/test_joiner.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/agents-analysts-llm.md` P3-02 (`TickerVerdict.model_validate({**raw_v, "ticker": ticker})` at `news/joiner.py:81`, `fundamental/joiner.py:92`)
- **Confidence:** low
- **Description:**
  The joiners build `{**raw_v, "ticker": ticker}` so the watchlist ticker silently overrides whatever the LLM put in `raw_v["ticker"]`. If the LLM emitted the wrong ticker (e.g. picked up a multi-ticker context window mistake), the override masks it. Nothing logs the mismatch. No test asserts that the override happens (so a future refactor removing it would not break anything) and no test asserts that a mismatch is logged (because the joiner doesn't log one yet). Low severity because the multi-ticker context-leak failure mode requires upstream context bleed too, but worth pinning.
- **Suggested action:**
  Add a test that supplies `raw_v["ticker"] = "WRONG"` and asserts (i) the joined verdict has `ticker == "AAPL"` (current behaviour) *and* (ii) a `caplog` warning records the mismatch (after the source fix lands). Contingent on source P3-02.

## Cross-subsystem dependencies

- **Source-fix landing dependency:** P0-01, P0-02, P1-03 all hinge on source P1-01/P1-02 surfacing fixes landing in the same PR. Without the source fix, the new tests would over-specify behaviour the code doesn't yet emit; with the source fix, the existing `test_fetch_degrades_on_provider_error` tests will start failing. These must be co-planned.
- **Contract-doc dependency:** P2-04, P2-05 depend on the §A row addition for `*_evidence` keys landing in `docs/contract-invariants.md` — file the row addition under that subsystem's audit, but the test additions can land in the same PR as the source surfacing fix.
- **Layout consolidation dependency:** P2-01, P2-02, P3-01 are a single layout migration (move prompt tests into `tests/unit/agents/analysts/<area>/`); they should land as one PR independent of source fixes.
- **Reference-only `_role_rank` retirement:** `tests/unit/contract/extractors/test_fundamental.py:192-196` asserts `hasattr(fund_mod, "_role_rank") is False` — this is a Phase 7 cleanup pin in the *extractor* audit's scope, not this one. Flag for the consolidator.
