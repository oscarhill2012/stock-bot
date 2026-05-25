# T-F09 — Contract package parallel-fixture cleanup

**Wave:** 4 (parallel)
**Pairs source-audit fix:** F9 (contract-package C2 parallel-payload-shape collapse, alias-key consolidation, dead-field deletion)
**Branch:** `fix/T-F09-contract-parallel-fixtures`
**Depends on:** T-F07 (SmartMoney delete — removes the smart_money
fixture / extractor sites entirely from this spec's scope). T-F10
(layout sweep — relocates six contract-adjacent tests from
`tests/unit/` root into `tests/unit/contract/`).
**Estimated diff size:** medium

## Scope

The `src/contract/` package has accumulated three classes of parallel
data shapes: (a) extractors that branch on multiple raw-payload
shapes where only one shape is actually emitted by production
fetch agents, (b) back-compat alias feature keys where two keys
store identical values but only one is consumed downstream, and
(c) one dead schema field (`AnalystEvidence.raw_text`) that no
production path writes or reads. Pick one shape per site, delete the
others, and migrate every test fixture / inline payload onto the
surviving shape. The smart_money sites in the contract package
become N/A because T-F07 deletes them.

### In scope

- **Fundamental extractor — pick the live shape.** The
  source-audit pick is the legacy Form4Bundle shape
  (`agents-strategist.md` neighbour, source-audit `contract.md` P1-01
  records production fetch_agent emits Form4Bundle, not flat-list).
  - Delete `_insider_aggregates_from_flat`, `_derivative_aggregates`,
    and the `"insider_trades"` branch in
    `src/contract/extractors/fundamental.py:344-404, 481-572, 579-691`.
  - Update the extractor's module docstring to describe only the
    Form4Bundle shape; drop the "Phase 7 preferred" paragraph at
    `:579-590`.
  - Delete the matching tests:
    - `tests/unit/contract/extractors/test_fundamental.py:121-273`
      (4 tests — flat-list insider trades).
    - `tests/unit/test_extract_fundamental_features.py:203-221`
      (`test_senior_officer_aggregate_via_flat_list`).
- **Technical extractor — collapse `_resolve_bars` to the single
  live branch.** Production writer emits
  `{"price_history": ph.model_dump()}` where `ph_payload["bars"]` is
  the list; that is branch 2 of three.
  - Rewrite `src/contract/extractors/technical.py:251-279`
    (`_resolve_bars`) to read directly from
    `raw["price_history"]["bars"]`; delete the top-level `"bars"` and
    the `"history"` / flat-list `price_history` branches.
  - Update the module docstring at lines 12-17.
  - Rewrite every test fixture / inline payload in
    `tests/unit/contract/extractors/test_technical.py:77-164, 205-352`
    from `{"bars": [...]}` to `{"price_history": {"bars": [...]}}`.
    Delete `test_handles_short_history_gracefully` at lines 58-70
    (the dead flat-list legacy case).
  - **Closes the T4 gap** the test-audit `contract-package.md` P1-09
    flags: today *zero* tests defend the live production shape.
- **News extractor — pick one alias name, delete the other.** Per
  source-audit `contract.md` P1-03, the strategist prompt reads
  `headline_polarity_mean_7d` (the alias), not `headline_polarity_mean`
  (the documented primary). The disposition is:
  **pick `headline_polarity_mean` (the shorter, undated name) as the
  single canonical name; drop the `_7d` alias and migrate the
  strategist-prompt consumer.**
  - `src/contract/extractors/news.py:29-30, 193-194` — drop the
    `_7d` alias emission.
  - `src/contract/strategist_prompt.py:276` — migrate the
    "Mean polarity" bullet read from `headline_polarity_mean_7d` to
    `headline_polarity_mean`.
  - Test assertions:
    - `tests/unit/contract/extractors/test_news.py:50-57` — drop the
      `_7d` half of the symmetric assertion.
    - Test-audit `contract-package.md` P1-04 lines (:69, :100)
      already assert on the primary key alone — keep as-is.
- **News extractor — three-key alternative cleanup.** Per
  source-audit `contract.md` P2-03, drop the `"articles"` and
  `"news_items"` fallbacks; production writes `{"news": [...]}`.
  - `src/contract/extractors/news.py:131` — collapse the OR-chain to
    `raw.get("news") or []`.
  - Update the module docstring at `:8, 83-85, 130-131` (source P2-05
    is the docstring-only side; folded in here per source-audit P2-03's
    "If P2-03 lands, this finding collapses into it.").
  - Rewrite `tests/fixtures/contract/news_aapl.json` from the
    `"news_items"` shape to `{"news": [...]}` with Finnhub-shaped
    fields.
  - Update inline payloads at
    `tests/unit/contract/extractors/test_news.py:66, 74` and the
    `test_news_reads_sentiment_field_not_polarity` test at lines
    82-100 from `"articles"` → `"news"`.
- **News extractor — delete `social_volume_z` dead key.** Per
  source-audit `contract.md` P2-04.
  - `src/contract/extractors/news.py:31, 205-211` — drop the key
    from `_KEYS` and the passthrough.
  - `src/contract/strategist_prompt.py:278` — drop the `NEWS_BULLETS`
    entry.
  - Delete tests
    `tests/unit/contract/extractors/test_news.py:60-62`
    (`test_social_volume_z_passthrough`) and `:72-75`
    (`test_handles_missing_social_volume`).
  - Drop the `"social_volume_z": 1.4` line from
    `tests/fixtures/contract/news_aapl.json` (handled in the same
    fixture rewrite as the previous bullet).
- **Social extractor — pick the alias name.** Mirror of the news
  alias decision. Per source-audit `contract.md` P1-03 the strategist
  reads `aggregate_score` (the alias), not `social_aggregate_score`
  (the primary). The disposition is:
  **pick `aggregate_score` (the load-bearing name) as the single
  canonical name; drop `social_aggregate_score` and rename the
  emission to `aggregate_score`.**
  - `src/contract/extractors/social.py:52-53, 102-103, 160-161` —
    drop the `social_aggregate_score` emission; rename existing
    write to `aggregate_score`.
  - Test assertions:
    - `tests/unit/contract/extractors/test_social.py:28` — drop the
      `social_aggregate_score` assertion (the "primary" name being
      deleted).
    - `tests/unit/contract/extractors/test_social.py:63-74`
      (`test_social_aggregate_score_back_compat_alias`) — delete
      entirely; once one name survives there is no alias to test.
    - `tests/unit/test_extract_social_features.py:17-21`
      (`test_extractor_emits_expected_keys`) — drop the
      `social_aggregate_score` element from the expected key set.
- **Delete `AnalystEvidence.raw_text`** (source-audit `contract.md`
  P2-01). No production writer or reader.
  - `src/contract/evidence.py:160-184` — delete the field declaration
    and docstring paragraph.
  - Delete `tests/unit/contract/test_evidence_raw_text.py` entirely.
  - Delete the `raw_text=None` line from
    `tests/unit/agents/strategist/test_evidence_view_missing_report.py:48`
    if T-F05 has not already deleted that file (T-F05 does delete
    it — coordinate at merge time).
- **Strengthen the four weak `test_extracts_required_keys` tests**
  per test-audit `contract-package.md` P2-02. Pair each
  `set(features.keys()) == set(_KEYS)` assertion with
  `assert sum(abs(v) for v in features.values()) > 0` so a
  regression that silently returned the zero-fallback fails the test.
  (The four files are
  `tests/unit/contract/extractors/test_{fundamental,news,smart_money,
  technical}.py:21-47`; smart_money is N/A — T-F07 deletes it.)

### Out of scope

- The smart_money extractor's `.get()`-on-Pydantic crash path
  (source-audit `contract.md` P1-04) — N/A because T-F07 deletes
  smart_money entirely.
- The smart_money extractor's `"filings"` / `"transactions"` alias
  fallbacks (source P2-02) — N/A, deleted by T-F07.
- The technical `state` parameter docstring (source P2-06) and the
  `_extract_stats_features` parameter rename (source P3-01) — too
  minor to bundle; defer.
- The `derive_social_verdict` magic char-truncations (source P3-02) —
  config-convention change; defer.
- The lazy-import comment de-duplication (source P3-03) — cosmetic.
- The `test_invariants_doc_carveout.py` doc-substring test — owned
  by T-F05 (it ships with the contract-invariants doc edit).
- The strategist-prompt layout tests' section-slice strengthening
  (test-audit `contract-package.md` P2-07) — defer; not blocking the
  parallel-shape collapse.
- The flat-`tests/unit/test_*.py` layout consolidations — owned by
  T-F10 (layout sweep) and the smart_money / SmartMoney deletion
  by T-F07.

## Findings closed

| Finding ID | File | Description |
|---|---|---|
| `contract.md` source P1-01 | `src/contract/extractors/fundamental.py` | Collapse to Form4Bundle shape; delete flat-list branch |
| `contract.md` source P1-02 | `src/contract/extractors/technical.py:251-279` | Collapse `_resolve_bars` to single live branch |
| `contract.md` source P1-03 (news half) | `src/contract/extractors/news.py`, `strategist_prompt.py:276` | Drop `_7d` alias; migrate prompt reader |
| `contract.md` source P1-03 (social half) | `src/contract/extractors/social.py` | Drop `social_aggregate_score`; keep `aggregate_score` |
| `contract.md` source P2-01 | `src/contract/evidence.py:160-184` | Delete `AnalystEvidence.raw_text` |
| `contract.md` source P2-03 | `src/contract/extractors/news.py:131` | Drop `"articles"` / `"news_items"` fallbacks |
| `contract.md` source P2-04 | `src/contract/extractors/news.py:31, 205-211`; `strategist_prompt.py:278` | Delete `social_volume_z` |
| `contract.md` source P2-05 | `src/contract/extractors/news.py:8, 83-85, 130-131` | Docstring update (collapses into P2-03) |
| `contract-package.md` test P1-01 | `tests/unit/contract/extractors/test_fundamental.py`, `test_extract_fundamental_features.py` | Delete flat-list tests |
| `contract-package.md` test P1-03 | `tests/fixtures/contract/news_aapl.json`, `test_news.py` | Rewrite fixture to `{"news": [...]}` shape |
| `contract-package.md` test P1-04 | `tests/unit/contract/extractors/test_news.py` | Drop loser-side alias assertion |
| `contract-package.md` test P1-05 | `tests/unit/contract/extractors/test_social.py`, `test_extract_social_features.py` | Drop loser-side alias assertion |
| `contract-package.md` test P1-06 | `tests/unit/contract/extractors/test_technical.py` | Rewrite to live `_resolve_bars` shape |
| `contract-package.md` test P1-07 | `tests/unit/contract/test_evidence_raw_text.py` | Delete file |
| `contract-package.md` test P1-08 | `tests/unit/contract/extractors/test_news.py:60-62, 72-75` | Delete `social_volume_z` tests |
| `contract-package.md` test P2-02 | four extractor test files | Add `sum(abs(v)) > 0` content guard |

(Smart_money-touching findings — source P1-04, P2-02 and test P1-02,
P1-09 — are **N/A** because T-F07 deletes the smart_money subsystem.
This spec assumes T-F07 has landed first.)

## Implementation steps

1. **Audit T-F07's actual disposition first.** Re-read the merged
   T-F07 diff to confirm the smart_money extractor file is gone, the
   smart_money fixture file is gone, and the smart_money entries in
   `_KEYS` / strategist-prompt bullets are gone. If any survivor
   exists, defer to a follow-up rather than re-litigating T-F07's
   scope.
2. **Land the fundamental collapse.** Source change + test deletions
   in one commit. The fundamental tests under
   `tests/unit/contract/extractors/test_fundamental.py:46-307` (the
   Form4Bundle-shaped ones) survive — verify they cover the cases
   the deleted flat-list tests covered, raise as a sub-finding if a
   coverage gap appears.
3. **Land the technical `_resolve_bars` collapse.** Source change +
   test fixture rewrites in one commit. This **adds** coverage of
   the live shape that today has zero — call this out in the commit
   body.
4. **Land the news cleanups in one commit:**
   - Three-key alternative collapse.
   - `headline_polarity_mean_7d` alias deletion + strategist_prompt
     consumer migration.
   - `social_volume_z` deletion.
   - Fixture rewrite (`news_aapl.json`).
   - Test deletions / assertion drops.
5. **Land the social alias collapse.** Source rename + test
   assertion drops in one commit.
6. **Land the `AnalystEvidence.raw_text` deletion.** Source field
   drop + test file deletion in one commit. Coordinate with T-F05
   (which also touches
   `test_evidence_view_missing_report.py:48`) — whichever lands
   second drops the `raw_text=None` line from the test cleanup it
   inherits.
7. **Add the `sum(abs(v)) > 0` content guard** to the three
   remaining `test_extracts_required_keys` tests (fundamental, news,
   technical; smart_money is gone).
8. **Run full `pytest tests/`**. Update
   `graphify-out/graph_delta.md` with the deleted symbols
   (`_insider_aggregates_from_flat`, `_derivative_aggregates`,
   `AnalystEvidence.raw_text`, `social_volume_z` etc.) and renamed
   fixture file.

## Acceptance criteria

- [ ] Full `pytest tests/` green.
- [ ] `ruff check src/` clean.
- [ ] Every finding in the table above is closed (cite by ID in
  commit body).
- [ ] `grep -rn "_insider_aggregates_from_flat\|insider_trades.*list\|
  raw_text\|social_aggregate_score\|headline_polarity_mean_7d\|
  social_volume_z\|raw\[.articles.\]\|news_items" src/ tests/`
  returns no hits (subagent should run this exact grep before the
  commit).
- [ ] The technical extractor has at least one test exercising the
  live `{"price_history": {"bars": [...]}}` shape.
- [ ] Graphify delta entry appended.

## Verification commands

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run --window baseline-2025-09 --tick-limit 1
```

## Risks and rollbacks

- **Risk: the strategist prompt consumer migration silently breaks
  the rendered prompt** (e.g. `strategist_prompt.py:276` was reading
  the alias because the alias survived a previous rename and the
  primary was never populated). Mitigation: confirm
  `tests/unit/contract/test_strategist_prompt_layout.py` exercises
  the "Mean polarity" bullet content — if it does not, add the
  assertion in this PR. Same applies to the social
  `aggregate_score` migration at line 294.
- **Risk: the news fixture rewrite breaks unrelated tests** that
  glob over `tests/fixtures/contract/`. Mitigation: full-suite run
  is the empirical check; subagent reads any failing test and
  rewrites the fixture consumer rather than reverting the cleanup.
- **Risk: T-F07 has not actually deleted everything this spec
  assumes is gone.** Mitigation: step 1 audits T-F07's diff; if
  smart_money survives, the smart_money-touching findings get
  re-included here (defer the spec until T-F07 is fully resolved).
- **Rollback:** feature branch discardable. Each step is a clean
  commit boundary.

## Subagent dispatch prompt sketch

> Implement T-F09 (contract parallel-fixture cleanup) per
> `docs/Phase11-project-audit/fix-plan/T-F09-contract-parallel-fixtures.md`. Context:
> `docs/Phase11-project-audit/source-audit/contract.md`,
> `docs/Phase11-project-audit/test-audit/contract-package.md`,
> `docs/test-policy.md` §A.7 / §E. Co-ordinate with T-F05 on the
> `test_evidence_view_missing_report.py` overlap. Pre-flight check
> from step 1 — verify T-F07 deleted smart_money before starting.
> British English throughout.
