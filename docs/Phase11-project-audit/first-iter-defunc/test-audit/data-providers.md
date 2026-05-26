# Test audit — src/data/providers/

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/data-providers.md` (primary); `docs/Phase11-project-audit/source-audit/SUMMARY.md` (Open Question #2 — unused data domains)
**Test files in scope:** 24
**Tests collected from those files:** 193 (via `pytest <paths> --collect-only -q`)
**Findings:** 3 P0 · 2 P1 · 7 P2 · 1 P3

## Files in scope

Grouped by location:

- `tests/unit/data/providers/` — 17 files
  - `test_analyst_consensus_yfinance.py`
  - `test_company_ratios_pit_composite.py`
  - `test_earnings_finnhub_as_of.py`
  - `test_filings_edgar_as_of.py`
  - `test_insider_trades_edgar_as_of.py`
  - `test_news_alpha_vantage_as_of.py`
  - `test_news_finnhub_as_of.py`
  - `test_news_tiingo.py`
  - `test_notable_holders_edgar_as_of.py`
  - `test_options_yfinance_shell.py`
  - `test_politician_trades_fmp.py`
  - `test_politician_trades_quiver_as_of.py`
  - `test_short_interest_finra_as_of.py`
  - `test_social_sentiment_finnhub_as_of.py`
  - `test_stats_yfinance_as_of.py`
  - `test_stats_yfinance_bulk.py`
  - `test_stats_yfinance_pit_adjust.py`
- `tests/unit/data/` — 5 files exercising the provider boundary
  - `test_registry.py`
  - `test_provider_registration.py`
  - `test_providers_split.py`
  - `test_provider_switching.py`
  - `test_dispatch_passes_as_of.py`
  - `test_as_of_threading.py`
  - `test_config_data_json.py`
- `tests/contract/` — 3 files
  - `test_provider_shapes.py`
  - `test_http_timeout_sourced_from_config.py`
  - `test_lookbacks_sourced_from_config.py`
- `tests/unit/` (root-level) — `test_form4_parser.py` exercises `_parse_form4` in `data/providers/insider_trades/edgar.py`

Layout note: the EDGAR Form 4 parser is split between `tests/unit/test_form4_parser.py` (low-level `_parse_form4` / `_is_planned_sale` / `_extract_footnote`) and `tests/unit/data/providers/test_insider_trades_edgar_as_of.py` (high-level `fetch`, `_build_trade`, `_build_derivative`). That asymmetry is in T8 below.

## Summary

The provider-layer suite is one of the most thorough in the repo — 193 tests across 24 files, with strong stubs at the leaf seam (`_fetch_company_news`, `_iter_filings`, `_list_form4_filings`, `httpx.AsyncClient`) and good coverage of PIT semantics, chunking, and shape contracts. But it has a textbook silent-failure blind spot: none of the EDGAR providers has a test that forces a per-row build failure and asserts the surfacing behaviour, so the three `except Exception: continue` branches (source `P1-01`, `P1-02`, `P1-03`) are completely undefended. The Finnhub social-403 path (source `P1-04`) is "covered" by a single `assert result is not None` that wouldn't catch any of the failure modes the source audit flags. Secondary themes: the contract shape test does not introspect leaf-provider `as_of` signatures (source `P2-02`), there is no test pinning the `options/yfinance` `symbol`-vs-`ticker` asymmetry (source `P2-03`), and `tests/unit/test_form4_parser.py` lives outside the provider tree even though its target is `data/providers/insider_trades/edgar.py`.

## Findings

### P0-01 · T4 missing surfacing test · EDGAR filings provider drops per-filing build failures with no test

- **Location(s):** new test needed; closest existing file is `tests/unit/data/providers/test_filings_edgar_as_of.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` P1-01 (`src/data/providers/filings/edgar.py:288` `except Exception: continue`)
- **Confidence:** high
- **Description:**
  `test_filings_edgar_as_of.py` has six tests stubbing `_iter_filings` with handcrafted `_FakeFiling` objects, all of which build successfully. None of them forces `_build_filing_with_identity` (the call inside the `try` at `edgar.py:285-287`) to raise — so the silent `continue` on line 289 is a fully untested branch. The source audit flags this as a canonical silent-degradation attractor: a structural edgartools change or per-filing parse error would shorten the returned list without any log line, and the strategist would see "fewer filings" rather than "the parse broke". The current happy-path tests would all continue to pass with the `continue` removed *or* extended to swallow new exception classes — the test suite cannot distinguish either case from correct behaviour.
- **Suggested action:**
  Add a test (e.g. `test_filings_edgar_surfaces_per_filing_build_failure`) that monkeypatches `_iter_filings` to return two fake filings, monkeypatches `_build_filing_with_identity` to raise on one of them, and asserts the failure surfaces — either as a raise from `fetch`, or via a `caplog`-recorded ERROR/WARNING naming the filing accession, depending on which surfacing primitive lands with the P1-01 source fix. The test should also assert the second filing is still returned (i.e. surfacing is per-row, not abort-all). Pair with the source fix PR.

### P0-02 · T4 missing surfacing test · EDGAR insider-trades provider swallows Form 4 parse failures twice with no test

- **Location(s):** new test needed; closest existing files are `tests/unit/data/providers/test_insider_trades_edgar_as_of.py` and `tests/unit/test_form4_parser.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` P1-02 (`src/data/providers/insider_trades/edgar.py:636-637` returns empty `Form4Bundle()`; line 698 outer-loop `except Exception: continue`)
- **Confidence:** high
- **Description:**
  Two stacked silent-failure attractors and zero tests for either. `test_insider_trades_edgar_as_of.py` exercises `fetch` only with stubbed `_list_form4_filings` returning `[]` — the inner `_fetch_and_parse_one` is never made to fail, and the outer `except Exception: continue` is never entered. Identical to P0-01: an edgartools shape change would degrade the trade list to silently-empty without surfacing. The `test_form4_parser.py` tests are at the `_parse_form4` layer and stub the form4 input directly, so they also never exercise the `_fetch_and_parse_one` exception path. The strategist consumes a neutral-looking `Form4Bundle(trades=[])` indistinguishable from "issuer has no insider trades".
- **Suggested action:**
  Add two tests pair-aligned to the source-audit fix. (a) `test_insider_trades_surfaces_per_filing_parse_failure` — monkeypatch `_list_form4_filings` to return two stub filings, monkeypatch `_fetch_and_parse_one` to raise on one of them (or, post-fix, return a structured failure marker), and assert the failure surfaces. (b) `test_insider_trades_surfaces_outer_loop_failure` — same setup but inject the failure between `_fetch_and_parse_one` and the bundle merge, exercising the `edgar.py:698` `except` branch. Both should assert the survivor filing's trades are still present, confirming surfacing is per-row.

### P0-03 · T3 + T4 · Social-sentiment 403 path "covered" by a near-empty assertion

- **Location:** `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py:9-22` (single `test_fetch_accepts_as_of_kwarg`)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` P1-04 (`src/data/providers/social_sentiment/finnhub.py:79-86` returns empty `SocialSentiment` on `FinnhubAPIException`)
- **Confidence:** high
- **Description:**
  The entire test file is 22 lines, with one test whose only assertion is `assert result is not None`. The comment says "No FINNHUB_API_KEY assumed — provider soft-fails to empty SocialSentiment" but the test never exercises the 403/`FinnhubAPIException` branch — it depends on the environment lacking a key, which is fragile (the test will pass differently depending on whether `FINNHUB_API_KEY` happens to be set). More importantly, the silent-failure attractor named in source P1-04 — neutral `aggregate_score=0.0` indistinguishable from "no social mentions" — is exactly what the test fails to assert against. Per test-policy §A.7, this is a "didn't raise, therefore it works" test on a path that *does* return a misleading neutral signal. A future change that swallows a different exception class would still pass `assert result is not None`. The cross-pollination concern in P2-02 source-audit also applies: this test currently relies on the dispatcher-tolerant `**_unused` kwarg passthrough and does not pin it to a specific behaviour.
- **Suggested action:**
  Replace the existing test with two: (a) `test_fetch_raises_or_marks_unavailable_on_premium_403` — monkeypatch `_fetch_social` to raise `finnhub.FinnhubAPIException("403 Forbidden")` and assert either a raise (preferred per `feedback_silent_failures_loud_tests`) or a structured marker (`is_unavailable=True` flag) — whichever the P1-04 source fix lands. The test must distinguish "endpoint unavailable" from "no social mentions". (b) `test_fetch_with_real_payload_returns_populated_snapshots` — monkeypatch `_fetch_social` to return synthetic reddit/twitter rows and assert `len(snapshots) > 0` and `aggregate_score != 0.0`, pinning the happy-path positive output state.

### P1-01 · T8 + T4 · Contract shape test does not introspect `as_of` signature drift

- **Location:** `tests/contract/test_provider_shapes.py` (entire file)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` P2-02 (mixed `date` vs `datetime` annotations across 14 providers)
- **Confidence:** high
- **Description:**
  The contract test verifies *return* shapes but does not assert on provider *signatures*. Source P2-02 documents that the `as_of` parameter type splits `date` vs `datetime` across 14 leaf providers; the wrappers normalise via `resolve_as_of(...)` so today nothing breaks, but the asymmetry is exactly the "drift that test-policy §G.3 stubs at the leaf hide" pattern called out in source P2-02. Most importantly, this is the file already named in the user-memory `feedback_provider_switching_must_be_one_line` ("every registered data provider shares one signature") — yet the contract test does not enforce that property. The audit prompt explicitly asks whether the shape parity test catches the `date` vs `datetime` asymmetry; it does not.
- **Suggested action:**
  Extend `tests/contract/test_provider_shapes.py` with a third parametrised contract test (e.g. `test_provider_as_of_annotation_is_datetime`) that, for every `(domain, name)` in `_REGISTRY`, introspects `inspect.signature(entry.fn).parameters["as_of"].annotation` and asserts it equals `datetime` (post-fix) or at minimum matches the canonical type chosen by the P2-02 source fix. Add a similar test for the first positional argument name (`ticker` not `symbol`) to defend source `P2-03`. Both belong here, not in per-provider unit tests, because they are layer-boundary invariants.

### P1-02 · T4 · No test pins the news-provider window-contract asymmetry

- **Location(s):** new test needed; would naturally live in `tests/contract/test_provider_shapes.py` or a sibling `test_news_provider_contract.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-providers.md` P2-04 (news providers have asymmetric `from_date`/`to_date` contracts)
- **Confidence:** high
- **Description:**
  Three news providers, two distinct contracts — `finnhub` and `tiingo` require `from_date`/`to_date`; `alpha_vantage` defaults them and derives from `lookback_days`. No test asserts the contract symmetry. `test_news_finnhub_as_of.py`, `test_news_tiingo.py`, and `test_news_alpha_vantage_as_of.py` each test their own provider's behaviour in isolation; nothing in the suite would catch a future caller stubbing one news provider and discovering at runtime that the kwargs don't match. Per the user memory `feedback_provider_switching_must_be_one_line`, this is exactly the contract that needs an automated guard. The shape test in `test_provider_shapes.py` could host this, but currently doesn't.
- **Suggested action:**
  After the P2-04 source fix (aligning all three news providers on the required-window contract), add a parametrised contract test that for every `("news", name)` in `_REGISTRY` introspects the signature and asserts `from_date` and `to_date` have the same default-vs-required status across all three. Block landing the source fix on this test.

### P2-01 · T8 layout · `test_form4_parser.py` lives outside the provider test tree

- **Location:** `tests/unit/test_form4_parser.py`
- **Source-audit cross-ref:** none directly; layout convention from test-policy §B
- **Confidence:** high
- **Description:**
  This file imports from `data.providers.insider_trades.edgar` (functions `_parse_form4`, `_extract_footnote`, `_is_planned_sale`) but lives in `tests/unit/` rather than `tests/unit/data/providers/`. Per test-policy §B mirror-the-source-tree rule, it belongs under `tests/unit/data/providers/`. Its presence as a root-level `tests/unit/*.py` makes it easy to miss when grepping for Form 4 coverage (which is how the prompt called it out as a discoverability concern). Consolidating with `test_insider_trades_edgar_as_of.py` is the natural move — both files cover the same EDGAR insider-trades provider at different layers.
- **Suggested action:**
  Move to `tests/unit/data/providers/test_insider_trades_form4_parser.py`, or merge into `test_insider_trades_edgar_as_of.py` if the two end up sharing fixtures. Defer to the eventual cleanup-PR consolidator.

### P2-02 · T6 · `test_pit_adjust_*` lives in the providers tree but tests pandas-internal behaviour against mod globals

- **Location:** `tests/unit/data/providers/test_stats_yfinance_pit_adjust.py`
- **Source-audit cross-ref:** none
- **Confidence:** medium
- **Description:**
  Two tests exercising `_pit_adjust` against synthetic pandas frames. These are unit tests of a private helper inside `stats/yfinance.py`. The scope is fine (no monkeypatching of class-level state), but the structural detail is that they directly access `mod._pit_adjust` from outside the module — a private helper exposed only because the test reaches into it. Not a violation today, but a clear candidate for either (a) exposing a public seam, or (b) inlining the test into a top-level test_stats_yfinance.py that owns all the yfinance helper unit tests rather than splitting them across three files (as_of, bulk, pit_adjust).
- **Suggested action:**
  Consolidate the three `test_stats_yfinance_*.py` files into one or two. Optional — low priority.

### P2-03 · T3 · `test_dispatch_passes_as_of.py` and `test_as_of_threading.py` assert only on dispatcher passthrough, not output content

- **Location:** `tests/unit/data/test_dispatch_passes_as_of.py:13-99` (all 5 tests); `tests/unit/data/test_as_of_threading.py` (8 parametrised tests)
- **Source-audit cross-ref:** none directly; test-policy §A.7 and §E "Asserting only on counts, never on content"
- **Confidence:** medium
- **Description:**
  The dispatch-cleanliness tests assert only that calling the wrapper does not TypeError and that the result is `[]` / `bundle.trades == []`. They stub the leaf to return empty, which is the right level — but every assertion is "didn't raise" plus an empty-result check. None of them assert that `as_of` actually reached the leaf with the expected value, or that the wrapper's PIT-resolution happened. `test_as_of_threading.py` does exactly this — it asserts `m.await_args.kwargs.get("as_of") == FIXED`. So `test_as_of_threading.py` provides the real defence; `test_dispatch_passes_as_of.py` is redundant with it plus the per-provider tests. Could be deleted or strengthened to assert the same property `test_as_of_threading.py` already covers, just one layer deeper.
- **Suggested action:**
  Either delete `test_dispatch_passes_as_of.py` (covered by `test_as_of_threading.py` + per-provider `test_*_accepts_as_of_kwarg` tests), or strengthen each test to capture the `as_of` value at the leaf and assert it matches the wrapper's input.

### P2-04 · T1 conditional · Tests for `earnings`, `analyst_consensus`, `short_interest`, `options` are conditional dead

- **Location:** `tests/unit/data/providers/test_earnings_finnhub_as_of.py`, `test_analyst_consensus_yfinance.py`, `test_short_interest_finra_as_of.py`, `test_options_yfinance_shell.py`
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/SUMMARY.md` Open Question #2 ("pull unused data domains" strategic decision)
- **Confidence:** medium
- **Description:**
  Per the audit prompt, these four domains are wired but unused (no agent consumes them). The tests themselves are well-written — sensible mocking at `httpx.AsyncClient`, good positive-content assertions, PIT filters covered. They are not currently dead; they defend live registry entries. But if the source-audit "pull unused domains" decision lands, all four delete together. Filing as conditional T1 so they sweep with the source PR rather than being missed.
- **Suggested action:**
  Hold; revisit once the SUMMARY Open Question #2 decision is made. If "remove": delete in the same PR. If "keep": no action.

### P2-05 · T7 hard-rule borderline · `test_config_data_json.py` reads the live `config/data.json`

- **Location:** `tests/unit/data/test_config_data_json.py:24, 40`
- **Source-audit cross-ref:** none directly; test-policy §A.6 ("Tests own their state")
- **Confidence:** high
- **Description:**
  Two tests read `Path("config/data.json")` directly from the live tree. Test-policy §A.6 forbids mutating the live `config/` tree, but reading is arguably necessary for a contract test that asks "does the shipped config resolve in the registry". The tests do not modify the file. The risk is indirect: if a future contributor adds a new provider to `config/data.json` without updating the registry, the test correctly catches it — that's the whole point. But the test will also break if `config/data.json` is renamed, moved, or restructured, in a way that a `tmp_path`-isolated test would not. Borderline acceptable; flagging for visibility.
- **Suggested action:**
  Leave as-is; it's the right test for the right invariant. If the project ever ships multiple config layouts or environments, isolate via `monkeypatch.chdir(tmp_path)` and a synthesised config file.

### P2-06 · T8 · `test_news_alpha_vantage_as_of.py` and `test_earnings_finnhub_as_of.py` each ship a copy of `_AsyncCM`

- **Location:** `tests/unit/data/providers/test_news_alpha_vantage_as_of.py:21-53`; `test_earnings_finnhub_as_of.py:17-48`; `test_short_interest_finra_as_of.py:25-56`; `test_provider_shapes.py:45-72`
- **Source-audit cross-ref:** none
- **Confidence:** high
- **Description:**
  The `_AsyncCM` async context-manager helper is now duplicated in four test files (the two `as_of` files explicitly note "if this helper appears in a third test file, hoist it into `tests/unit/data/providers/conftest.py`"). The threshold has been crossed.
- **Suggested action:**
  Hoist `_AsyncCM` and `_make_fake_resp` into `tests/unit/data/providers/conftest.py` as a shared fixture or module-level helper. Remove the four local copies.

### P2-07 · T3 · `test_filings_edgar_as_of.test_fetch_accepts_extra_kwargs` is a "didn't raise" test

- **Location:** `tests/unit/data/providers/test_filings_edgar_as_of.py:74-85`
- **Source-audit cross-ref:** test-policy §E ("It didn't raise, therefore it works")
- **Confidence:** medium
- **Description:**
  `assert out == []` is the only assertion. The test verifies the dispatcher-extra-kwargs contract (`**_unused`) but degenerates into "the function returned empty list when stubbed to return empty". The same shape appears in `test_notable_holders_edgar_as_of.test_fetch_accepts_unrecognised_kwargs`, `test_insider_trades_edgar_as_of.test_fetch_swallows_unrecognised_kwargs`. None are individually fatal; together they're a pattern. Consider asserting *positively* that the unknown kwarg was discarded (e.g. by checking it's not in the captured kwargs dict on the stubbed leaf).
- **Suggested action:**
  Strengthen by capturing kwargs at the stubbed leaf and asserting the spurious kwarg name is absent from the leaf's call args. Low priority.

### P3-01 · T8 · `test_provider_shapes.py` `_LIVE_PENDING` / `_CACHE_PENDING` are empty sets

- **Location:** `tests/contract/test_provider_shapes.py:553, 557`
- **Source-audit cross-ref:** none
- **Confidence:** high
- **Description:**
  Both sets are empty. The xfail-management scaffolding works correctly when populated, but the comments still reference "Phase B alignment tasks" that have all landed. The infrastructure can stay (future contract drift will reuse it), but the references should be updated or removed.
- **Suggested action:**
  Either drop the `_PENDING` sets and the `_live_params`/`_cache_params` helpers in favour of a plain `parametrize("domain", sorted(DOMAIN_SHAPES))`, or leave them and update the comments to "Reserved for future contract drift". Cosmetic.

---

## Cross-subsystem notes for the consolidator

- P0-01, P0-02, P0-03 are all silent-failure attractors waiting for the source-audit P1 fixes in `docs/Phase11-project-audit/source-audit/data-providers.md`. The tests should land **with** the source fix PR, not as a separate batch — otherwise the source-fix PR has nothing defending the new surfacing behaviour and a follow-up could silently revert it.
- P1-01 (contract shape signature introspection) interacts with the user memory `feedback_provider_switching_must_be_one_line` — this is the file where that invariant *should* be enforced.
- P2-04 (conditional T1) depends on the consolidator-level decision in `SUMMARY.md` Open Question #2. Hold until that resolves.
- No T7-A.1 violations found (no test in scope makes outbound API calls without an `RUN_LLM_TESTS=1`-equivalent gate or `pytest.mark.slow`). `test_finra_integration_real_network` and `test_analyst_consensus_integration_real_network` are correctly `@pytest.mark.slow` and skip cleanly when credentials are absent.
- No T5 violations found (no test stubs `data.providers.registry` or `CachedDataStore` for fakery; all leaf stubs are correctly at `_fetch_*`, `_iter_filings`, `_list_form4_filings`, or `httpx.AsyncClient`). The suite is well-disciplined on test-policy §A.5.
- The "Stubbing the wrong news provider" anti-pattern (§E) is **not** present — `test_dispatch_passes_as_of.test_get_stock_news_dispatches_cleanly` explicitly tracks the active provider (was `tiingo` → `alpha_vantage`) and stubs whichever one config currently names.
