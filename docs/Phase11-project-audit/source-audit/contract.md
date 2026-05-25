# Source audit — src/contract/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 8 (`__init__.py`, `evidence.py`, `digest.py`, `digest_defaults.py`, `ticker_evidence.py`, `strategist_prompt.py`, `extractors/__init__.py`, `extractors/_sector_map.py`, `extractors/news.py`, `extractors/fundamental.py`, `extractors/technical.py`, `extractors/smart_money.py`, `extractors/social.py`)
**Findings:** 0 P0 · 4 P1 · 6 P2 · 3 P3

## Summary

The `src/contract/` package holds the shared Pydantic schemas
(`AnalystEvidence`, `AnalystVerdict`, `TickerEvidence`, etc.), the
deterministic digest aggregator, the strategist prompt-block renderer, and
five per-domain feature/verdict extractors. Three themes dominate the
findings: (1) the extractors have accumulated **parallel raw-payload shapes**
(Phase 5 legacy ⇄ Phase 7 canonical) where production callers feed only one
shape and the other branch is unreached; (2) several **back-compat alias
feature keys** survive (`headline_polarity_mean` vs `_7d`,
`aggregate_score` vs `social_aggregate_score`) with at least one alias
load-bearing in `strategist_prompt.py` — deleting the "primary" key would
silently break the prompt; (3) a `raw_text` field added to
`AnalystEvidence` is never written or read in `src/`. No findings rise to
P0 — none of the dead/parallel paths can change today's pipeline outputs
because production deterministically picks one branch — but the parallel
shapes are exactly the silent-failure attractor template flagged in the
user memory and §A.7 of `test-policy.md`. Cross-subsystem note: several
findings depend on confirming what `agents/analysts/*/fetch.py` and
`agents/analysts/*/joiner.py` actually emit; that subsystem's audit can
either confirm dead-status or surface a writer I missed.

## Findings

### P1-01 · C2 parallel old/new branches · Fundamental extractor's two raw-payload shapes

- **Location:** `src/contract/extractors/fundamental.py:579-691` (the dispatch on `"insider_trades" in raw`); `_insider_aggregates_from_flat` at `:344-404` (Phase 7 flat-list path) vs `_extract_insider_features_legacy` at `:481-572` (Form4Bundle path).
- **Confidence:** high
- **Description:**
  The extractor documents two payload shapes — Phase 7 "flat-list"
  (`raw["insider_trades"]`, `raw["insider_derivative_trades"]` as lists of
  `InsiderTrade.model_dump()` dicts) and legacy Phase 5
  (`raw["insider"]` as a typed `Form4Bundle`). The docstring at `:590`
  calls the flat-list path "preferred for new providers". However, the
  only production writer of `temp:fundamental_data` is
  `src/agents/analysts/fundamental/fetch_agent.py:177`, which emits
  `{"ratios": ..., "filings": [...], "insider": Form4Bundle(...)}` — i.e.
  the **legacy** shape. The flat-list `_insider_aggregates_from_flat` +
  `_derivative_aggregates` branch is exercised only by tests
  (`tests/unit/contract/extractors/test_fundamental.py:153,186,228,267` and
  `tests/unit/test_extract_fundamental_features.py:218`). Two complete
  insider-aggregation implementations coexist with subtly different
  semantics (the flat path windows derivatives by `filed_at`; the legacy
  path treats derivative counts as point-in-time and not window-filtered —
  see `:558-560` comment vs `:444-466`). One bad merge or
  fetch-agent edit silently switches branches.
- **Suggested action:**
  Pick the shape that matches what fetch_agent actually emits (legacy
  Form4Bundle) as the single supported shape; delete
  `_insider_aggregates_from_flat`, `_derivative_aggregates`, and the
  `"insider_trades"` branch in `extract_fundamental_features`, plus
  update tests. Alternatively, if the intent is to migrate
  fetch_agent.py to emit the flat list, do that migration first and then
  delete the legacy branch — but do not leave both alive.

### P1-02 · C2 parallel old/new branches · Technical extractor's three raw-payload shapes

- **Location:** `src/contract/extractors/technical.py:251-279` (`_resolve_bars`).
- **Confidence:** high
- **Description:**
  `_resolve_bars` checks three locations: Phase 7 canonical (`raw["bars"]`),
  Phase 5 nested (`raw["price_history"]["bars"]` or
  `raw["price_history"]` as a flat list), and "very old legacy"
  (`raw["history"]`). The single production writer
  (`src/agents/analysts/technical/fetch.py:82-85`) emits
  `{"price_history": ph_payload, "ratios": cr_payload}` where `ph_payload`
  is `ph.model_dump()` of a `PriceHistory` (which contains a `bars`
  attribute). So branch 2 (`isinstance(ph_payload, dict)` →
  `ph_payload.get("bars")`) is the only one actually hit; branches 1
  (`"bars"` top-level) and 3 (`"history"` or flat-list `price_history`)
  are unreached by any producer. The module docstring at lines 12-17
  describes these as concurrent shapes, but they are an evolution of one
  shape across the same fetch agent.
- **Suggested action:**
  Collapse `_resolve_bars` to the single live branch (look for
  `raw["price_history"]["bars"]`), delete the other branches, and update
  the module docstring. If the goal is to migrate `fetch.py` to emit
  `raw["bars"]` directly, do that migration in the same pass.

### P1-03 · C2 parallel old/new branches · Back-compat alias feature keys

- **Location:**
  - `src/contract/extractors/news.py:29-30, 193-194` — `headline_polarity_mean` + `headline_polarity_mean_7d` (alias).
  - `src/contract/extractors/social.py:52-53, 102-103, 160-161` — `social_aggregate_score` + `aggregate_score` (alias).
- **Confidence:** high
- **Description:**
  Each pair stores **identical values** under two names. For news, the
  comment at `news.py:29` says `headline_polarity_mean` was "renamed from
  headline_polarity_mean_7d" — but `strategist_prompt.py:276` still reads
  the **old** name (`headline_polarity_mean_7d`) for the "Mean polarity"
  bullet, so the `_7d` alias is load-bearing. For social, the comment
  pattern is reversed (`social_aggregate_score` is the named primary,
  `aggregate_score` is the alias) but the strategist prompt at
  `strategist_prompt.py:294` again reads only the alias
  (`aggregate_score`). Deleting either alias silently empties one bullet
  in the strategist prompt. The two pairs read on opposite sides of the
  rename, which suggests they accreted independently rather than as a
  single migration. Tests assert both pairs match (e.g.
  `tests/unit/contract/extractors/test_news.py:57`,
  `tests/unit/contract/extractors/test_social.py:74`), pinning the alias
  pair in place but not its purpose.
- **Suggested action:**
  Pick one name per concept (probably the shorter
  `headline_polarity_mean` and `aggregate_score` for consistency with
  the existing strategist-prompt registry), drop the alias, and update
  the consumer in `strategist_prompt.py` plus tests accordingly.

### P1-04 · C5 silent-failure attractor · smart_money extractor dict-access on Pydantic instances

- **Location:** `src/contract/extractors/smart_money.py:309-322` (the `for f in filings:` loop calling `f.get("filer_id")`, `f.get("side")`, `_amount(f)`).
- **Confidence:** medium
- **Description:**
  The smart_money agent at
  `src/agents/analysts/smart_money/agent.py:142-145` passes
  `ticker_raw.politicians` straight into `raw["politician_trades"]`.
  `ticker_raw.politicians` is a `list[PoliticianTrade]` — Pydantic model
  instances, **not** dicts. The extractor then calls `f.get("filer_id")
  or f.get("filer")`, `f.get("side")`, etc. Pydantic v2 `BaseModel`
  instances do not implement `.get()`; calling it raises
  `AttributeError`. Additionally, `PoliticianTrade` has no `filer_id`,
  `filer`, `amount`, or `dollar_value` field
  (`src/data/models/trades.py:12-23` — it has `amount_min_usd` and
  `amount_max_usd`). The branch is hidden today only because the
  `politician_trades` provider is intentionally disabled
  (`feedback_silent_failures_loud_tests` user memory and per
  `feedback_politician_trades_disabled`), so `politicians_list` is
  always `[]` and the `if filings:` guard skips the loop. Re-enabling
  politicians (the obvious future change) would raise on the first
  ticker. Flagged `medium` because the trigger is gated; once the
  provider returns data, this becomes a P0 crash.
- **Suggested action:**
  Either change the smart_money agent to pass
  `[p.model_dump() for p in politicians_list]` (matching the dict-based
  extractor contract), or change the extractor to handle both Pydantic
  instances and dicts uniformly. Either way, also reconcile the
  field-name mismatch (`PoliticianTrade.amount_min_usd` vs the
  extractor's lookup of `"amount"` / `"dollar_value"`) — today the
  extractor would silently return 0 for every politician trade even
  after the AttributeError fix.

### P2-01 · C1 dead code · AnalystEvidence.raw_text never written or read

- **Location:** `src/contract/evidence.py:160-184` (the `raw_text` field on `AnalystEvidence`).
- **Confidence:** high
- **Description:**
  `AnalystEvidence.raw_text` is declared as
  `str | None = Field(default=None, max_length=10_000)` with an extensive
  docstring describing its purpose as an "optional pass-through of the
  raw provider text the LLM analyst saw". `grep -rn "raw_text" src/`
  returns only the declaration and the docstring — there is **no
  writer** in `src/` (only `None`-explicit `raw_text=None` in test
  fixtures at `tests/unit/agents/strategist/test_evidence_view_missing_report.py:48`
  and `tests/unit/contract/test_evidence_raw_text.py`). The contract
  schema preserves the field, but no production path populates it and
  no production path consumes it. Likely a leftover from
  commit `92b865a feat(analysts): wrap Fund/News in
  YieldingAnalystWrapper + add raw_text` which was superseded by the
  Phase 9 per-ticker joiner fan-out (commits `9bd1...` / `7590...`)
  without removing the field.
- **Suggested action:**
  Confirm with the analyst-agents subsystem audit that no current
  agent writes `raw_text`; if confirmed, delete the field, its
  docstring paragraph, and the schema-shape test
  `tests/unit/contract/test_evidence_raw_text.py`.

### P2-02 · C1 dead code · Smart-money extractor's `filings` / `transactions` aliases

- **Location:** `src/contract/extractors/smart_money.py:290-297` (the `filings = raw.get("filings") or raw.get("transactions") or raw.get("politician_trades")` chain).
- **Confidence:** high
- **Description:**
  The only production writer of `raw` for the smart_money extractor is
  `src/agents/analysts/smart_money/agent.py:142-145`, which emits
  `{"politician_trades": ..., "notable_holders": ...}` — `"filings"` and
  `"transactions"` are never written. The `or` chain therefore never
  resolves through the first two keys. (Tests do not exercise those
  aliases either — `grep -rn '"filings":\|"transactions":' tests/ src/`
  in the smart-money path returns only the extractor and one unrelated
  fundamental-report-cache match.)
- **Suggested action:**
  Drop the `filings` / `transactions` fallbacks; read `raw.get("politician_trades")` directly. Update the docstring at `:260-264` accordingly.

### P2-03 · C1 dead code · News extractor's `articles` / `news_items` aliases

- **Location:** `src/contract/extractors/news.py:131` (`raw.get("articles") or raw.get("news_items") or raw.get("news") or []`).
- **Confidence:** high
- **Description:**
  The only production writer of `temp:news_data` is
  `src/agents/analysts/news/fetch_agent.py:92` which emits
  `news_data[ticker] = {"news": serialised}`. The first two
  alternatives in the chain — `"articles"` (described in the docstring
  as "Phase 7 canonical") and `"news_items"` (described as "legacy
  alias") — are unreached by any producer in `src/`. Tests reach the
  `news_items` branch via fixtures (`tests/fixtures/contract/news_aapl.json`,
  `tests/unit/contract/extractors/test_news.py:66`) but no production code
  does. The docstring's claim that `articles` is "Phase 7 canonical"
  contradicts what fetch_agent actually writes — separate C7 concern.
- **Suggested action:**
  Drop the `articles` and `news_items` fallbacks; read `raw.get("news")`
  directly. Update the docstring and the alias tests to match.

### P2-04 · C1 dead code · `social_volume_z` feature flows nowhere from production

- **Location:** `src/contract/extractors/news.py:31, 205-211` and `src/contract/strategist_prompt.py:278` (the `NEWS_BULLETS` entry `("social_volume_z", "Social volume z:", _plain, None)`).
- **Confidence:** high
- **Description:**
  `social_volume_z` is read from `raw.get("social_volume_z")` in the
  news extractor, but no news provider in `src/data/providers/news/`
  ever sets that key — `grep -rn '"social_volume_z"' src/` finds only
  the extractor itself, the strategist-prompt bullet registry, and the
  test fixture `tests/fixtures/contract/news_aapl.json` which seeds it
  by hand. In production the value is always `0.0` from
  `_zero_features()`, and the strategist always sees the bullet
  `"Social volume z: 0.0"`. The social analyst (separate package) has
  its own `mention_count_total`/`mention_count_reddit`/etc. that fill
  the same conceptual slot.
- **Suggested action:**
  Drop the `social_volume_z` key from `_KEYS` in `news.py`, drop the
  passthrough at `:205-211`, and drop the `NEWS_BULLETS` entry in
  `strategist_prompt.py:278`. (Tests at
  `tests/unit/contract/extractors/test_news.py:60-75` will need
  updating.)

### P2-05 · C7 doc/code drift · `news.py` docstring labels production shape as "legacy"

- **Location:** `src/contract/extractors/news.py:8, 83-85, 130-131`.
- **Confidence:** high
- **Description:**
  The module docstring at `:8` says "Accepts `articles` as a canonical
  key alongside the legacy `news_items` alias", and the docstring at
  `:83-85` calls `raw["articles"]` "Phase 7 canonical" and
  `raw["news_items"]` "legacy alias". Neither is what actually flows
  through production — the live writer
  (`src/agents/analysts/news/fetch_agent.py:92`) emits
  `{"news": [...]}`. So the "canonical" alias in the doc is unused, and
  the alias that production *does* use (`"news"`) is described as a
  third-priority fallback only.
- **Suggested action:**
  Update the docstring to reflect what fetch_agent.py emits today. If
  P2-03 lands, this finding collapses into it.

### P2-06 · C7 doc/code drift · `extract_technical_features.state` docstring is stale

- **Location:** `src/contract/extractors/technical.py:311-314`.
- **Confidence:** medium
- **Description:**
  The `state` parameter is documented as "Phase 7 pipeline state dict —
  currently unused but accepted so callers can pass it without error
  (Fix C / relative-strength will wire it in Phase 5)." The code at
  `:416-451` clearly uses `state["reference_prices"]` (Fix C is wired);
  the docstring still describes the parameter as unused. Probably a
  comment that did not get updated after the relative-strength wiring
  landed.
- **Suggested action:**
  Rewrite the `state` docstring paragraph to describe the actual
  consumed keys (`state["reference_prices"]` for the SPY / sector ETF
  PIT clamping).

### P3-01 · C2 doc-code drift · `_extract_stats_features` parameter still named `stats` after the rename

- **Location:** `src/contract/extractors/fundamental.py:158-174`.
- **Confidence:** high
- **Description:**
  The helper accepts a `stats` parameter and the docstring carefully
  explains that "stats" is the historical name for what is now called
  "ratios" at the caller. The helper would read more cleanly with the
  parameter renamed to `ratios` — the rename was applied at the
  payload-key level (Phase 5) but not at the helper-signature level.
  Cosmetic.
- **Suggested action:**
  Rename the `stats` parameter (and the internal `stats_sub` variable
  at `:660`) to `ratios`; drop the apologetic docstring paragraph.

### P3-02 · C6 config-convention violation · Magic char-truncations in `derive_social_verdict`

- **Location:** `src/contract/extractors/social.py:273, 290, 296, 311, 317` (the `[:160]`, `[:69]`, `[:575]`, `[:1150]` truncations).
- **Confidence:** low
- **Description:**
  `derive_social_verdict` truncates rationale to `[:160]`, driver name
  to `[:69]`, driver body to `[:575]`, summary to `[:1150]`. These are
  the schema cap values (from `config/analysts.json` via
  `output_caps.report_driver_name_max_chars` etc.) but hardcoded
  numerically here rather than referenced from
  `_OUT`/`_schema_cap` as the schema declarations in `evidence.py:70-97`
  do. If anyone tunes the caps in config, the synthetic-report
  truncation here silently drifts out of sync. Low confidence because
  these may have been intentionally locked to the historical schema
  cap, but rule-wise this is config drift.
- **Suggested action:**
  Replace the literals with references to the same
  `get_analysts_config().output_caps.report_*_max_chars` values that
  `evidence.py` uses; or, if the truncation is meant to be a tighter
  cap than the schema, document why in a comment.

### P3-03 · C7 doc/code drift · `derive_technical_verdict` lazy-import comment lists circular import that may no longer apply

- **Location:** `src/contract/extractors/technical.py:36-44` and `:494-497` (also `smart_money.py:45-52, 391-393`, `social.py:36-44, 191-193`).
- **Confidence:** low
- **Description:**
  Three extractor modules each contain near-identical lazy-import
  comments describing a circular import chain
  (`contract.extractors.X ← agents.analysts.heuristics ←
  agents.analysts.__init__ ← X.agent ← contract.extractors.X`). The
  pattern is duplicated literally. Worth verifying the chain still
  exists — the per-ticker fan-out refactor (commits `9bd1`/`7590`) may
  have broken one of the links such that the lazy import is no longer
  needed in some of the three modules. If the chain still applies, a
  single canonical comment would be clearer than three near-copies.
  Cosmetic, but a footprint for drift.
- **Suggested action:**
  Sanity-check whether the circular import still triggers if the
  imports are moved to module top; if not, hoist them and delete the
  comments. If they are still needed, factor the explanation into a
  short shared note (e.g. in `src/contract/extractors/__init__.py`)
  and have each module reference it.
