# Test audit — `src/data/models/` + top-level `src/data/`

**Auditor:** subagent
**Date:** 2026-05-25
**Source-audit cross-references:** `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md`
**Test files in scope:** 18 (full list below)
**Tests collected from those files:** 101 (via `pytest <paths> --collect-only -q`)
**Findings:** 0 P0 · 5 P1 · 5 P2 · 1 P3

## Files in scope

Grouped by directory.

`tests/unit/data/models/` — eight files, one per Pydantic model module the
test author covered:

- `tests/unit/data/models/test_analyst_consensus.py`
- `tests/unit/data/models/test_company_ratios.py`
- `tests/unit/data/models/test_earnings.py`
- `tests/unit/data/models/test_filings.py`
- `tests/unit/data/models/test_news.py`
- `tests/unit/data/models/test_short_interest.py`
- `tests/unit/data/models/test_smart_money.py`
- `tests/unit/data/models/test_trades.py`

`tests/unit/data/` (top-level data layer, excluding `providers/`) — nine files:

- `tests/unit/data/conftest.py` (the `registry_isolation` fixture)
- `tests/unit/data/test_active_pacing.py`
- `tests/unit/data/test_as_of_threading.py`
- `tests/unit/data/test_company_ratios.py` (duplicates models/ coverage —
  see P2-01)
- `tests/unit/data/test_config.py`
- `tests/unit/data/test_config_data_json.py`
- `tests/unit/data/test_dispatch_passes_as_of.py`
- `tests/unit/data/test_price_history.py`
- `tests/unit/data/test_provider_registration.py`
- `tests/unit/data/test_provider_switching.py`
- `tests/unit/data/test_providers_split.py`
- `tests/unit/data/test_registry.py`
- `tests/unit/data/test_registry_swap.py`
- `tests/unit/data/test_secrets.py`
- `tests/unit/data/test_timeguard.py`
- `tests/unit/data/test_timeguard_fallback_counter.py`

Root-level (mis-located per §B):

- `tests/unit/test_insider_model_roundtrip.py` — round-trip + reject-unknown
  tests for `InsiderTrade`, `InsiderDerivativeTrade`, `Form4Bundle`; should
  live under `tests/unit/data/models/`.

## Summary

The model-side suite is dominated by symmetric "construct minimal · construct
fully · round-trip · defaults-to-None" triplets, one per model. They are
honest tests but very low-yield — they exercise Pydantic's own machinery,
not any project-side validator, and four of the modules they cover
(`earnings`, `analyst_consensus`, `short_interest`, `options`) are wired-but-
unreachable per source-audit P1-02. The top-level suite is in much better
shape: `test_timeguard*`, `test_secrets`, `test_active_pacing`,
`test_provider_switching`, and `test_dispatch_passes_as_of` each exercise a
real seam with positive assertions. Two notable gaps: (1) no test exists for
the `OptionContract` model (so the unused-domain frontier from source P1-02
has eight files of tests for the other three but a hole for the fourth);
(2) no test exists for `is_missing_timestamp` itself, so the source-audit
P1-04 misbehaviour (`is_missing_timestamp(None) == True`, contradicting the
"strongly typed datetime" claim) is neither pinned nor refuted, and the
"call-site is typed everywhere" assumption underpinning the source-audit
fix is unverified.

## Findings

### P1-01 · T1 dead-domain frontier · model round-trip tests for unreachable Phase 3 domains

- **Location(s):**
  - `tests/unit/data/models/test_analyst_consensus.py` (6 tests, 70 lines)
  - `tests/unit/data/models/test_earnings.py` (5 tests, 59 lines)
  - `tests/unit/data/models/test_short_interest.py` (4 tests, 52 lines)
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md`
  P1-02 (the four Phase 3 domains are registered but never dispatched).
- **Confidence:** high
- **Description:**
  Three of the four unused Phase 3 domains have dedicated model test files
  in `tests/unit/data/models/`. The fourth (`options`) has none — see
  P2-04. These files contain only minimal/fully-populated construction
  checks and `model_validate(model_dump())` round-trips. If the
  consolidator decides (a) to delete the four unused-domain provider
  modules and models per source P1-02, every test in these three files
  becomes dead: imports from `data.models.analyst_consensus`,
  `data.models.earnings`, and `data.models.short_interest` will fail to
  resolve. If the consolidator decides (b) to wire the domains into an
  analyst, the tests still need strengthening because they assert nothing
  beyond Pydantic's own round-trip — they would not catch a real consumer
  bug. Either way the present shape is wrong: as T1 conditional on the
  source decision, they should be the first dropped if deletion wins.
- **Suggested action:**
  Mark for conditional deletion alongside source P1-02 cleanup PR. If the
  alternative (wire-in) path is chosen, hold the files but rewrite each
  test to assert against the consuming extractor's expected feature shape
  rather than just round-trip equivalence.

### P1-02 · T1 dead-domain provider-shape test branches · contract test holds the only live callers

- **Location(s):** `tests/contract/test_provider_shapes.py:286–356` (the
  four `if domain == "..."` branches for `earnings`, `analyst_consensus`,
  `short_interest`, `options`) and the `_LIVE_ONLY` set at line 548.
- **Source-audit cross-ref:**
  `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md` P1-02.
- **Confidence:** high
- **Description:**
  Source-audit P1-02 explicitly notes that the contract test is the only
  surviving caller of these four domain dispatch paths. If the source
  cleanup PR chooses deletion, every branch under `_LIVE_ONLY = {"earnings",
  "analyst_consensus", "short_interest", "options"}` becomes dead test
  code. The contract test is not "in scope" for this audit's directory
  (top-level + models/), but the test author needs to know the dependency
  exists so the deletion lands atomically. Filing it under data/models
  because that's where the model imports it references live.
- **Suggested action:**
  Track as a paired deletion alongside the source P1-02 PR. The contract
  test is shared infrastructure; the deletion PR must surgically remove
  those four branches and the `_LIVE_ONLY` membership rather than the
  whole file.

### P1-03 · T1 dead test of retired `data.providers.stats` re-export shape · test_providers_split

- **Location:** `tests/unit/data/test_providers_split.py` (the entire file,
  ~50 lines, two tests).
- **Source-audit cross-ref:** none directly — source-audit P2-01 notes the
  Phase 5 `stats/yfinance` reference is stale; this test still imports
  `from data.providers.stats import yfinance as prov` and patches
  `prov.yf.Ticker`.
- **Confidence:** medium
- **Description:**
  The test predates the Phase 5 data-model split and exercises the
  `_fetch_price_history` / `_fetch_company_ratios` pair on the
  `data.providers.stats.yfinance` module. The active providers per
  `config/data.json` are `price_history=yfinance` and
  `company_ratios=pit_composite`. The latter has its own dedicated test
  at `tests/unit/data/providers/test_company_ratios_pit_composite.py`,
  and the live `pit_composite` provider builds ratios from price history
  rather than calling `_fetch_company_ratios` on stats/yfinance. This
  file therefore guards the stats-module-as-it-was-pre-Phase-5 split, and
  the assertion "both call paths share one `Ticker` construction via
  `_yt_raw`" is only meaningful if anything still calls
  `_fetch_company_ratios`. `grep` shows the `stats/yfinance`
  `_fetch_company_ratios` symbol is referenced only by tests now —
  source-audit P2-01 calls out the same drift on the model docstring side.
  Out of scope to confirm (providers audit), but worth noting alongside
  the model-side cleanup.
- **Suggested action:**
  Cross-check with the providers test audit; if the stats/yfinance
  `_fetch_company_ratios` symbol has no live caller in `src/`, delete
  this file outright. If it still serves the historical PriceHistory
  path, rename the test to make the scope clear (it's exercising only
  the lru_cache sharing, not "both providers").

### P1-04 · T4 missing surfacing test · no test pins the `is_missing_timestamp(None)` misbehaviour

- **Location(s):** new test needed — should live at
  `tests/unit/data/models/test_missing.py`.
- **Source-audit cross-ref:** `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md`
  P1-04.
- **Confidence:** high
- **Description:**
  Source-audit P1-04 flags that `is_missing_timestamp(None)` returns
  `True` despite the module docstring claiming `MISSING_TIMESTAMP` exists
  so the codebase can keep `datetime` typing rather than `datetime |
  None`. No unit test for the helper exists anywhere — `grep -rn
  is_missing_timestamp tests/` shows two hits, both of them upstream
  callers in `tests/backtest/leak_regressions/` and
  `tests/unit/backtest/cache/`, neither of which exercises the helper
  itself. Production callers under `src/backtest/cache/store.py:391,478,
  566,793` pass `datetime` instances directly; the audit-side callers
  under `src/backtest/audit/{telemetry,upstream_verifier}.py` defensively
  pre-wrap with `value if isinstance(value, datetime) else None` — i.e.
  every call site forces the `None` branch to be dead. Since no test
  asserts what `is_missing_timestamp(None)` returns, the source-audit fix
  ("if every call site is typed, remove the `if value is None:` branch")
  has no regression net: if a caller is missed and a future refactor
  exposes the previously-dead `None` path, no test will catch it.
- **Suggested action:**
  Add `tests/unit/data/models/test_missing.py` covering:
  1. `MISSING_TIMESTAMP` is timezone-aware UTC AD 1.
  2. `is_missing_timestamp(MISSING_TIMESTAMP) is True`.
  3. `is_missing_timestamp(datetime(2023, 3, 10, tzinfo=UTC)) is False`.
  4. `is_missing_timestamp(None)` — **as part of the source fix PR**, flip
     this assertion from `is True` (today's behaviour) to either `is
     False` (if the `None` branch is removed) or raises `TypeError`
     (preferred — fail loud, per `test-policy.md §A.7`).

### P1-05 · T1 anchoring test of legacy `quiver` politician-trades path

- **Location:**
  `tests/unit/data/test_provider_switching.py::test_politician_trades_swap_fmp_to_quiver`
  (lines 100–138).
- **Source-audit cross-ref:**
  `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md` P1-03
  (`data/__init__.py` rate-limit table still references retired Quiver),
  plus the project memory `project_politician_trades_disabled` (no free
  historical source; commented out in `_build_provider_fns`).
- **Confidence:** medium
- **Description:**
  The test asserts a config-flip from `fmp` → `quiver` routes dispatch
  through the Quiver coroutine. Per the project memory the politician-
  trades domain is intentionally disabled in the live fetcher because no
  free historical source exists. The Quiver path still exists in source
  (`data/providers/politician_trades/quiver.py`) and is exercised by
  this test; it is one of the "parallel old/new branches" the test-
  policy §A.5 warns about — keeping the test green presupposes Quiver
  remains a viable target, which the project memory says it is not. Note
  that the *active* path is FMP and the file's other tests
  (`test_news_swap_finnhub_to_tiingo_uses_tiingo` and back) are
  legitimate provider-switching coverage.
- **Suggested action:**
  Either delete the Quiver swap test outright (the FMP soft-fail case is
  already exercised by `test_dispatch_passes_as_of.py::
  test_get_public_figure_trades_dispatches_cleanly`), or strengthen its
  docstring to record that it's a contract-only check that the swap
  *mechanism* works rather than a vote of confidence in Quiver as a
  live target. Prefer deletion alongside source-audit P1-03's rate-limit
  table refresh.

### P2-01 · T8 duplicate / parallel test coverage of `CompanyRatios`

- **Location(s):** `tests/unit/data/test_company_ratios.py` and
  `tests/unit/data/models/test_company_ratios.py`.
- **Source-audit cross-ref:** none direct (this is a layout finding).
- **Confidence:** high
- **Description:**
  Two sibling files with the same name cover the same model under two
  different angles: the top-level one tests the Phase 5 split round-trip
  + sparse-defaults; the models/ one tests Phase 1 extension defaults +
  the 10 new fields. The two files don't share assertions and together
  read like one cohesive test file split across two locations.
  `pytest` discovery doesn't collide on filename (they live in different
  packages), but the layout is confusing — a reader looking for "the
  CompanyRatios tests" has to find both, and a future contributor adding
  a new field test cannot tell which file to extend. Test-policy §B
  says unit tests "mirror the source tree"; the canonical home is
  `tests/unit/data/models/test_company_ratios.py`.
- **Suggested action:**
  Merge the two files at the canonical models/ path, retaining all
  assertions. Delete the top-level sibling. Land this alongside
  source-audit P2-01's docstring refresh.

### P2-02 · T6 wide-scope monkeypatch / mutating shared `_cache` in `test_provider_switching`

- **Location:** `tests/unit/data/test_provider_switching.py` lines 33–34,
  47–58, 72–76, 86–98, 113–117, 128–138.
- **Source-audit cross-ref:** none direct.
- **Confidence:** high
- **Description:**
  Each test mutates `data.config.get_config().providers[domain]` directly
  on the module-level cached `DataConfig` and restores in `finally`. This
  works under sequential test execution but violates test-policy §A.6
  ("Tests own their state — use `tmp_path` / `monkeypatch.setenv` /
  `monkeypatch.chdir` / `monkeypatch.setattr` so cleanup is automatic").
  The sibling test file `test_registry.py` and `test_active_pacing.py`
  do this correctly via `monkeypatch.setattr(data_config, "_cache",
  data_config.DataConfig(providers={...}))`. The try/finally pattern in
  `test_provider_switching.py` is fragile: an assertion failure mid-test
  could leak state into the next test before the `finally` restore runs
  (assertion failures do trigger `finally`, but other interpreter-level
  failures do not). The fixture `registry_isolation` already exists in
  `conftest.py` and is the right idiom here.
- **Suggested action:**
  Convert the three tests to use `monkeypatch.setattr(data_config,
  "_cache", ...)` instead of mutating the cached dict in-place. Either
  reuse `registry_isolation` (if registry mutations are involved) or add
  a sister `config_isolation` fixture.

### P2-03 · T3 weak-completion assertions across model round-trip tests

- **Location(s):** every `model_validate(model_dump())` test in
  `tests/unit/data/models/`:
  - `test_analyst_consensus.py::test_analyst_rating_round_trip`,
  - `test_earnings.py::test_earnings_report_round_trip`,
  - `test_filings.py::test_filing_new_fields_round_trip`,
  - `test_news.py::test_news_article_relevance_round_trip`,
  - `test_short_interest.py::test_short_interest_snapshot_round_trip`,
  - `test_company_ratios.py::test_company_ratios_new_fields_round_trip`,
  - `test_trades.py::*_round_trip` (×3).
- **Source-audit cross-ref:** none direct (test-policy §E "Asserting only
  on counts, never on content" analogue).
- **Confidence:** medium
- **Description:**
  Every round-trip test follows the shape `restored = M.model_validate(
  m.model_dump()); assert restored == m`. This asserts Pydantic itself
  round-trips its own data — it does not catch any project-side
  invariant. The interesting cases (does `model_dump(mode="json")` of a
  `datetime` field survive a JSON round-trip? does the
  `extra="forbid"` config catch typos on validate-from-dict? do
  `Literal`-typed enums round-trip cleanly?) are not covered. Only
  `test_insider_model_roundtrip.py::test_insider_trade_rejects_unknown_field`
  and `test_smart_money.py::test_smart_money_raw_rejects_unknown_field`
  test something stronger than Pydantic's own machinery. These tests
  satisfy the "construct without raising" form that test-policy §A.7
  explicitly calls out: present, green, but defending no real invariant.
- **Suggested action:**
  Strengthen by either (a) round-tripping through `model_dump_json()` /
  `model_validate_json()` and asserting field-by-field equality on
  datetime/date fields where ISO coercion can drop tzinfo; or (b)
  deleting the trivial round-trips and keeping only the
  reject-unknown-field tests (which actually exercise the project-side
  `extra="forbid"` config). Prefer (b) — round-trip-equality of pure
  Pydantic models is not a project invariant worth defending in tests.

### P2-04 · T4 missing model round-trip test for `OptionContract`

- **Location(s):** new test needed —
  `tests/unit/data/models/test_options.py` does not exist.
- **Source-audit cross-ref:**
  `docs/Phase11-project-audit/source-audit/data-models-and-top-level.md` P1-02.
- **Confidence:** medium
- **Description:**
  Three of the four Phase 3 unused-domain models have a dedicated
  test file in `tests/unit/data/models/` (earnings, analyst_consensus,
  short_interest). `OptionContract` does not. The omission is asymmetric:
  if the consolidator chooses to wire the four domains in (the
  alternative to deletion in source P1-02), the options model is the
  one without a test net. If the consolidator chooses deletion, no
  action is needed — the missing test won't be missed. Filed as a
  conditional P2: only matters if wire-in wins.
- **Suggested action:**
  Hold pending source P1-02 resolution. If wire-in wins, add a minimal
  round-trip test mirroring `test_short_interest.py`'s shape, but
  preferably re-shaped per P2-03 (test only the reject-unknown-field
  + Literal-validation invariants, not bare round-trip equality).

### P2-05 · T8 root-level test file should live under `tests/unit/data/models/`

- **Location:** `tests/unit/test_insider_model_roundtrip.py`.
- **Source-audit cross-ref:** none direct (layout/discoverability).
- **Confidence:** high
- **Description:**
  The file lives at root-level `tests/unit/` but its content is purely
  data-model tests for `InsiderTrade`, `InsiderDerivativeTrade`, and
  `Form4Bundle` — exactly the same scope as the rest of
  `tests/unit/data/models/test_trades.py`. The two files together cover
  the same model. Per test-policy §B unit tests "mirror the source
  tree"; this file's canonical home is alongside `test_trades.py`. Two
  separate files for one source module is also a borderline P2-01
  duplicate pattern.
- **Suggested action:**
  Merge into `tests/unit/data/models/test_trades.py` (the two files'
  assertions are non-overlapping — the root file tests the new
  `transaction_code` / `is_10b5_1` / `footnote` fields and
  `extra="forbid"`, while `test_trades.py` covers `is_officer` /
  `is_director` / Table II / NotableHolder body fields). Delete the
  root-level file.

### P3-01 · T8 / T3 hygiene · model tests test "field accepts value", not "validator rejects bad value"

- **Location(s):** every "fully_populated" test in
  `tests/unit/data/models/` — analyst_consensus, earnings, filings,
  short_interest, trades.
- **Source-audit cross-ref:** none direct.
- **Confidence:** low (cosmetic, but a recurring shape worth noting).
- **Description:**
  The "fully populated" tests check that every optional field is
  accepted when supplied a value, then assert two or three fields back.
  They never test the *negative* — that a malformed value raises
  `ValidationError`. For example, `AnalystRating.recommendation_mean`
  has no documented range and accepts any float, but if the schema is
  ever tightened to `Field(ge=1.0, le=5.0)`, none of the existing tests
  would notice. Filed as P3 because the absence of negative tests is
  not actively masking a current bug — there are no live range
  validators to defend.
- **Suggested action:**
  When the source-audit P2-01 docstring refresh lands, take the chance
  to add one negative test per model for any `Literal` or `Field(ge=,
  le=)` constraint already in source. Skip if no such constraint
  exists; do not invent constraints just to test them.

---

## Cross-subsystem dependencies for the consolidator

1. **P1-01 + P1-02 + P2-04** are blocked on source-audit P1-02's
   delete-vs-wire-in decision. They form one paired cleanup PR.
2. **P1-04** adds a new test file (`tests/unit/data/models/test_missing.py`)
   that should land alongside the source-audit P1-04 fix — the source
   fix changes behaviour, the test pins the new behaviour and prevents
   regression of either side.
3. **P1-05 + P1-03** are paired with source-audit P1-03's rate-limit
   table refresh. Quiver is no longer the active politician-trades
   provider; the test of the swap-to-Quiver path and the docstring
   reference to it should both be retired in one go.
4. **P1-02** affects `tests/contract/test_provider_shapes.py` —
   surface this in the contract-test audit so the deletion PR lands
   surgically rather than removing the whole file.
5. **P2-01 + P2-05** are pure layout consolidations; can land in a
   single test-only PR independent of any source-fix.
