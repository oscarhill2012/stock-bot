# Source audit — `src/data/models/` + top-level `src/data/`

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 21 (14 model files + 7 top-level files)
**Findings:** 0 P0 · 4 P1 · 8 P2 · 3 P3

## Summary

The data layer is the shared Pydantic schema spine plus a handful of
small support modules (rate limiter, retry wrapper, secrets reader,
timeguard, config loader, provider registry). Three themes dominate
the findings: (1) **a complete unused-domain frontier** — the four
Phase 3 domains (`earnings`, `analyst_consensus`, `short_interest`,
`options`) are wired end-to-end in `config/data.json`, `registry.py`,
`DOMAIN_SHAPES`, and have provider modules + Pydantic models, but no
agent or wrapper ever calls `dispatch` for them, so the only callers
are the contract test; (2) **C7 doc/code drift** around the
`NewsArticle.sentiment` field — the data model still documents it as
"populated by providers that perform per-article NLP" while the
project memory records that news sentiment is intentionally null with
the active Finnhub provider, and the downstream news extractor still
multiplies `0.0` through every `polarity_*` feature it emits;
(3) **mild C3 / C7 hangover** from the Phase 5 stats split — stale
"populated by stats/yfinance" references inside `CompanyRatios` and a
`SmartMoneyRaw` model that is reachable only by its submodule import,
not via `data.models.__init__`. No P0 findings — no contract
violations, no silent-failure attractors on a load-bearing path.

Cross-subsystem dependency for the consolidator: finding **P1-01**
spans the data layer (`NewsArticle.sentiment` docstring) and
`src/contract/extractors/news.py` (the consumer that bakes 0.0
sentiment into emitted features). The fix-plan must touch both.

## Findings

### P1-01 · C7 doc/code drift · `NewsArticle.sentiment` docstring contradicts the "news sentiment intentionally null" decision

- **Location:** `src/data/models/news.py:25-32`, cross-references
  `src/contract/extractors/news.py:149-203`,
  `src/data/providers/news/finnhub.py:260`,
  `src/data/providers/news/tiingo.py:174`.
- **Confidence:** high
- **Description:**
  The model field docstring says `sentiment` is "populated by providers
  that perform per-article NLP (e.g. Alpha Vantage, Finnhub)". Per the
  user memory `project_news_sentiment_intentionally_null` and the
  actual provider code, Finnhub (the active provider per
  `config/data.json`) sets `sentiment=None` unconditionally; Tiingo
  likewise; only Alpha Vantage (currently *not* the active provider
  and "AV unviable" per memory) populates it. The downstream extractor
  at `src/contract/extractors/news.py:153` reads `item.get("sentiment")
  or 0.0` and feeds the resulting always-zero values into every
  `pct_news_positive_7d`, `headline_polarity_mean`, and
  `headline_polarity_recency_weighted` feature it emits. So the model
  docstring tells the reader "this field carries signal", while in
  practice it is constant 0.0 and silently drags every polarity feature
  toward zero. This is C7 first (the docstring lies about Finnhub) and
  borders on C5 at the consumer (the extractor cannot distinguish "no
  sentiment available" from "neutral sentiment"), but the consumer is
  out of subsystem.
- **Suggested action:**
  Update the `NewsArticle.sentiment` docstring to record that the
  active Finnhub path leaves the field `None` by design and that the
  consuming extractor must therefore not treat `None`/`0.0` as a
  signal. Flag the extractor finding for the consolidator to route to
  `src/contract/extractors/news.py`.

### P1-02 · C1 dead-domain frontier · four Phase 3 domains are registered but never dispatched

- **Location:**
  Provider modules: `src/data/providers/earnings/finnhub.py`,
  `src/data/providers/analyst_consensus/yfinance.py`,
  `src/data/providers/short_interest/finra.py`,
  `src/data/providers/options/yfinance.py`.
  Registry rows: `src/data/registry.py:94-97` (`DOMAIN_SHAPES`),
  `src/data/registry.py:111-115` (`DOMAINS`).
  Config wiring: `src/data/config.py:27-30`, `config/data.json:11-14`.
  Models: `src/data/models/earnings.py`,
  `src/data/models/analyst_consensus.py`,
  `src/data/models/short_interest.py`,
  `src/data/models/options.py`.
- **Confidence:** high
- **Description:**
  All four domains pass the import-time validator in
  `data.__init__._validate_active_providers_are_registered()` and
  appear in `DOMAIN_SHAPES`, but `grep -rn 'get_earnings\|get_analyst_consensus\|get_short_interest\|get_options' src/ tests/ scripts/`
  returns nothing, and there is no `dispatch("earnings", ...)` /
  `dispatch("analyst_consensus", ...)` etc. call anywhere outside
  `tests/contract/test_provider_shapes.py`. There is no `get_*` wrapper
  function in `data/__init__.py` for any of the four — contrast with
  the eight original domains, every one of which has a wrapper at
  `data/__init__.py:80-327`. So the four providers run only as registry
  fixtures: they register a limiter, they get exercised by the
  contract test for shape conformance, and they are otherwise
  unreachable. The `options/yfinance.py` provider docstring even calls
  itself a "v1 shell" that returns an empty list. This is a sizeable
  surface (~12 files including tests) carrying its own model classes,
  provider modules, cache providers, and config entries with no
  end-to-end consumer. The cost of leaving it is that a reader cannot
  tell from the data layer alone which domains are load-bearing and
  which are scaffolding waiting for a future analyst.
- **Suggested action:**
  Either (a) add the four `get_*` wrappers + the analyst that needs
  them, or (b) delete the four domains' provider modules, models,
  registry rows, config keys, and contract-test branches in one cleanup
  PR. Defer the choice to consolidation — this depends on near-term
  roadmap intent.

### P1-03 · C7 doc/code drift · `data/__init__.py` module docstring references retired `politician_trades` provider semantics

- **Location:** `src/data/__init__.py:13-32` (the "Rate-limit budgets"
  table) cross-referenced with `config/data.json:8` and
  `config/README.md:136`.
- **Confidence:** medium
- **Description:**
  The module docstring's rate-limit table lists "Quiver: 30/min, 2s
  min interval" and uses Quiver as the source for the
  `min_decision_interval_seconds()` floor calculation. The active
  politician-trades provider per `config/data.json` is `fmp`, not
  `quiver`. The table also lists `EDGAR: 600/min` but no longer
  reflects the actual active providers (yfinance for price_history /
  company_ratios / analyst_consensus / options, Finnhub for news /
  social_sentiment / earnings, FINRA for short_interest, FMP for
  politician_trades, EDGAR for filings / insider_trades /
  notable_holders). The narrative claim "With edgartools direct EDGAR
  access ... the floor is now ~2s (Quiver)" is no longer true since
  Quiver isn't on the active set.
- **Suggested action:**
  Refresh the rate-limit table to mirror the current
  `config/data.json` provider set and re-derive the floor sentence
  from the actual `min_decision_interval_seconds()` output. Alternative:
  delete the table entirely and have the docstring point at
  `config/README.md` as the source of truth.

### P1-04 · C5/C7 attractor · `is_missing_timestamp(None)` returns `True`, contradicting the model's "strongly typed datetime" claim

- **Location:** `src/data/models/missing.py:28-44`.
- **Confidence:** medium
- **Description:**
  The module docstring says the sentinel exists so models keep
  `datetime` typing rather than `datetime | None`. But
  `is_missing_timestamp(None)` returns `True` with the explicit
  comment "for callers that haven't migrated to the sentinel yet". The
  helper therefore silently accepts the very `None`-typed value the
  sentinel was introduced to eliminate, which is a C5-style attractor:
  a provider that forgets to substitute `MISSING_TIMESTAMP` and leaves
  `published_at=None` will neither raise (Pydantic catches it) nor get
  rejected at the cache-write skip check (the helper returns `True`),
  but everywhere else in the codebase a `None` `datetime` would crash.
  The "haven't migrated" pathway is open-ended — there is no comment
  noting which caller still needs migration, and `grep` shows every
  current caller passes `datetime` instances. Either the migration is
  done and the `None` branch is dead, or it is not and there is a
  latent crash hazard.
- **Suggested action:**
  Audit the remaining callers (`src/backtest/cache/store.py:391, 478,
  ...`); if every call site is typed, remove the `if value is None:`
  branch so the helper enforces the "strongly typed" contract its
  docstring promises. Otherwise mark each non-migrated caller with a
  `TODO` referencing this finding.

### P2-01 · C7 doc/code drift · `CompanyRatios` references `stats/yfinance` provider for half its fields

- **Location:** `src/data/models/company_ratios.py:46-86`.
- **Confidence:** high
- **Description:**
  Field docstrings repeatedly say "populated by the stats/yfinance or
  pit_composite provider" and "populated by the stats/yfinance
  provider". The active `company_ratios` provider per
  `config/data.json:4` is `pit_composite`; `data/providers/stats/`
  still exists but is no longer wired to the `company_ratios` domain
  (only `price_history` registers from there). The docstring leaves a
  reader thinking both providers are equal candidates, when in fact
  `stats/yfinance` is the legacy split point. Same drift in
  `src/data/models/price_history.py:12` ("Replaces the `history` field
  of the retired `StockStats` model"): the Phase 5 transitional note
  is now archaeological.
- **Suggested action:**
  Update each affected docstring to name `pit_composite` as the
  current writer; drop the Phase 5 transition notes or move them to
  `docs/`.

### P2-02 · C2/C7 parallel data shape · `SocialSentiment` provider always returns the same default per-platform skeleton

- **Location:** `src/data/models/sentiment.py:9-26`,
  `src/data/providers/social_sentiment/finnhub.py:78-99`,
  `src/backtest/providers/social_sentiment_cache.py`.
- **Confidence:** medium
- **Description:**
  The Finnhub social-sentiment endpoint is premium-only and the
  provider soft-fails to `SocialSentiment(ticker=..., snapshots=[],
  aggregate_score=0.0)` on every free-tier call. The backtest cache
  provider does the same. The model itself is therefore degenerate in
  practice — every consumer always sees `snapshots=[]` /
  `aggregate_score=0`. This is closer to C7 (the model docstring
  implies a real shape that doesn't exist on the active path) than to
  C2, but it bears noting alongside the news-sentiment finding because
  both fall into the "model carries a payload no live provider ever
  fills" bucket. The social analyst at
  `src/agents/analysts/social/fetch.py` then unconditionally produces
  an `is_no_data` verdict and the strategist gets a constant neutral
  signal.
- **Suggested action:**
  Either annotate `SocialSentiment` to document that the active
  providers return empty snapshots by design, or — if the analyst is
  expected to derive value from non-empty data we don't yet have —
  treat this in the same triage pass as P1-02 (dead-domain frontier).

### P2-03 · C3 (low) · `Provider[T]` Protocol in `registry.py` has no consumers

- **Location:** `src/data/registry.py:43-56`.
- **Confidence:** low
- **Description:**
  The `Provider[T]` typing Protocol is defined but `grep -rn
  "registry.Provider\|: Provider\[" src/ tests/ scripts/` returns no
  hits. The internal `_Entry.fn` is typed `Callable[...,
  Awaitable[Any]]`, not `Provider[T]`. `DOMAIN_SHAPES` (which the
  Protocol references in its own docstring) is dict-typed. The
  Protocol carries no current load-bearing role — it is a
  type-documentation breadcrumb without a check. Marking low-confidence
  because Rule 7 architectural seams are exempt from C3, and "every
  provider implements this interface" may yet be enforced statically
  in a future audit.
- **Suggested action:**
  Either start typing `_Entry.fn` with the Protocol so something is
  actually checked, or delete it and let `Callable[..., Awaitable[Any]]`
  stand alone.

### P2-04 · C3 (low) · `data.retry.with_retry` has bi-modal exception handling that obscures intent

- **Location:** `src/data/retry.py:24-44`.
- **Confidence:** low
- **Description:**
  The module top-level guards `import httpx` / `import requests` with
  `except ImportError` and degrades `_RETRYABLE` to `(ConnectionError,
  TimeoutError)`. Both `httpx` and `requests` are first-order
  dependencies in `pyproject.toml`-class usage everywhere else in the
  data layer. The fallback path is for "tests / minimal envs" but the
  test suite explicitly requires both. The fallback can never fire in
  this project today; it adds branching the reader has to mentally
  unwind to understand the retry surface.
- **Suggested action:**
  Drop the try/except — let the import fail loudly if either dep is
  missing.

### P2-05 · C3 (low) · `data.secrets._ensure_loaded()` module-level state has one caller and no test

- **Location:** `src/data/secrets.py:17-25` cross-referenced with
  `src/backtest/runner.py:254`.
- **Confidence:** low
- **Description:**
  `_loaded` + `_ensure_loaded()` exist to memoise the `load_dotenv()`
  call. `require_key()` is the only caller; the backtest runner's
  comment says it doesn't need the lazy load. The memoisation buys
  nothing — `dotenv.load_dotenv()` is itself idempotent — and the
  module-level mutable state is the smallest possible surface for the
  benefit. Could simplify by calling `load_dotenv()` directly inside
  `require_key()`.
- **Suggested action:**
  Inline `load_dotenv()` into `require_key()` and delete the
  `_loaded` / `_ensure_loaded` pair. Behaviour-neutral.

### P2-06 · C7 doc/code drift · `data.__init__.py` mentions `docs/data-sources.md` which is no longer the canonical reference

- **Location:** `src/data/__init__.py:8-9, 16-17, 36-37`,
  `src/data/rate_limit.py:6-15`, `src/data/retry.py:3`.
- **Confidence:** medium
- **Description:**
  The cross-reference points the reader at `docs/data-sources.md` for
  the rate-limit caps and the no-direct-provider-imports rule. Per the
  graphify report (Community 57 nodes), the consolidated reference is
  now `docs/data-and-providers.md`. The old filename may still exist
  but the canonical doc has moved; readers chasing the link land on
  either a stub or an empty file.
- **Suggested action:**
  Update the three references (`data/__init__.py`, `rate_limit.py`,
  `retry.py`) to point at the consolidated `docs/data-and-providers.md`.
  Verify the old path is either deleted or annotated as historical.

### P2-07 · C7 (mild) · `data/__init__.py` re-exports `dispatch` as `_dispatch` but the underscore is misleading

- **Location:** `src/data/__init__.py:76` —
  `from .registry import dispatch as _dispatch  # noqa: F401  (re-export)`.
- **Confidence:** medium
- **Description:**
  The comment marks it as a re-export but the leading underscore is
  the Python convention for "private", which contradicts the re-export
  intent. No consumer imports `data._dispatch`; the seven `_dispatch(
  ... )` calls below it all use the private alias internally.
  Mechanically harmless, but the comment and the naming disagree.
- **Suggested action:**
  Either drop the `as _dispatch` alias and use `dispatch` internally,
  or drop the re-export comment if the intent is genuinely private.

### P2-08 · C7 (mild) · `SmartMoneyRaw` is not exported from `data.models.__init__`

- **Location:** `src/data/models/smart_money.py`,
  `src/data/models/__init__.py:5-28`.
- **Confidence:** high
- **Description:**
  Every other model class in `models/` is re-exported from
  `models.__init__` and aggregated into `__all__`. `SmartMoneyRaw` is
  not. The only consumer
  (`src/agents/analysts/smart_money/fetch.py:41`) imports it via the
  submodule path `from data.models.smart_money import SmartMoneyRaw`.
  This is asymmetry that will mislead the next contributor — they will
  either add another submodule-path import on the assumption that
  models can't be re-exported, or notice and ask why. Phase 7.6
  introduced the model but didn't wire it through the package
  surface.
- **Suggested action:**
  Add `SmartMoneyRaw` to `models/__init__.py`'s imports and `__all__`,
  and migrate the one current consumer to import via
  `from data.models import SmartMoneyRaw`.

### P3-01 · C7 · stale "Phase 5" / "Phase 7" / "Phase 9" tags in model docstrings

- **Location:**
  `src/data/models/company_ratios.py:73-86`,
  `src/data/models/filings.py:16-46`,
  `src/data/models/trades.py:50-99`,
  `src/data/models/news.py:30-32`.
- **Confidence:** high
- **Description:**
  Many field docstrings carry archaeological "Phase 7 additions (audit
  row 2.X)" markers. Future readers will not know what Phase 7 audit
  2.5 was; the markers preserve historic provenance at the cost of
  current readability. Cosmetic only.
- **Suggested action:**
  Land alongside any other touch to these files: strip phase tags from
  field-level docstrings; preserve provenance in commit history or in
  a single docs/ note.

### P3-02 · C7 · `news_lookback_days` and `filings_lookback_days` exist in `FetchDefaults` but no single doc lists which `defaults.*` fields each domain wrapper consumes

- **Location:** `src/data/config.py:34-46`,
  `src/data/__init__.py:185, 320`.
- **Confidence:** medium
- **Description:**
  `FetchDefaults` has nine fields. Three are consumed by
  `data/__init__.py` wrappers (`news_lookback_days`,
  `filings_lookback_days`, indirectly), four by the fundamental
  analyst's fetch (`insider_lookback_days`, `filings_per_form`,
  `include_filing_excerpts`), two by smart_money fetch
  (`politician_lookback_days`, `notable_holder_lookback_days`,
  `notable_holder_limit`). The model itself has no docstring tying
  each field to its consumer. A reader changing `FetchDefaults` cannot
  see at a glance who reads which field.
- **Suggested action:**
  Add a docstring per field naming the call site, or — cheaper — add
  a one-line `# read by: <consumer>` comment beside each declaration.

### P3-03 · C7 · `data.config._DOMAINS` is a separate mirror of `registry.DOMAINS` with a circular-import-avoidance comment

- **Location:** `src/data/config.py:14-31`.
- **Confidence:** high
- **Description:**
  `_DOMAINS` in `config.py` duplicates `DOMAINS` in `registry.py` to
  break a circular import at module load time. The mirror is
  maintained by convention: a comment says "must stay in sync". Today
  the two lists do match. Cosmetic finding — the duplication is the
  pragmatic fix and there's no obvious better shape — but worth a
  contract test or a `__post_init__`-style runtime check to catch
  drift if either list grows without the other.
- **Suggested action:**
  Add a one-line runtime cross-check (e.g. at the end of
  `data/__init__.py`'s validator block) that asserts
  `_DOMAINS == registry.DOMAINS`, so a divergence raises at import
  rather than at the first miss.

### Models

(Findings under §Models grouping: P1-01, P1-04, P2-01, P2-02, P2-08,
P3-01.)

### Top-level

(Findings under §Top-level grouping: P1-02, P1-03, P2-03, P2-04,
P2-05, P2-06, P2-07, P3-02, P3-03.)
