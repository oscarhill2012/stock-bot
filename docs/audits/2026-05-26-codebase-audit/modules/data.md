# Module audit — `data`

Audited 2026-05-26.  Source under audit: `src/data/`.  Tests:
`tests/unit/data/`, `tests/contract/test_provider_shapes.py`,
`tests/contract/test_lookbacks_sourced_from_config.py`,
`tests/contract/test_http_timeout_sourced_from_config.py`,
`tests/contract/test_wrappers_supply_lookback_to_cache.py`,
`tests/integration/backtest/test_strict_mode_aborts_on_missing_as_of.py`.

Authoritative §7.4 layering: 5 analyst-facing wrappers (`price_history`,
`company_ratios`, `news`, `social_sentiment`, `insider_trades`) + 3
smart-money fan-out provider domains (`notable_holders`, `filings`,
`politician_trades`) = 8 wired domains.  The four extra domains
(`earnings`, `analyst_consensus`, `short_interest`, `options`) are
**not in the authoritative count**, register live providers, are
listed in `config/data.json`, but have zero consumers.

---

## F-data-001
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/data/providers/earnings/finnhub.py`,
  `src/data/providers/analyst_consensus/yfinance.py`,
  `src/data/providers/short_interest/finra.py`,
  `src/data/providers/options/yfinance.py`, plus
  `src/data/models/{earnings.py,analyst_consensus.py,short_interest.py,options.py}`
- **Evidence:**
  ```
  $ grep -rn "get_earnings\|get_analyst_consensus\|get_short_interest\|get_options" src/ scripts/ | grep -v "src/data/"
  (no matches)
  $ grep -rn "EarningsHistory\|AnalystConsensusBundle\|ShortInterestSnapshot\|OptionContract" src/ scripts/ | grep -v "src/data/"
  (no matches outside data/)
  ```
  No domain wrapper exists for any of these four domains in
  `src/data/__init__.py` (the public surface); no agent imports their
  models; no `scripts/backtest_fetch.py` provider function is built;
  no `src/backtest/providers/*_cache.py` exists for them.  They are
  registered (via `data.providers.__init__`), listed in
  `config/data.json`, and validated by `_validate_active_providers_are_registered`,
  but produce nothing the rest of the codebase consumes.  `options/yfinance.py`
  is explicitly a placeholder that returns `[]` for every `as_of`
  (`src/data/providers/options/yfinance.py:88-93`).
- **Intent violated:** §7.4 (authoritative layer count is 8, not 12).
- **Suggested action:** delete the four provider modules, the four
  model modules, their entries in `_DOMAINS` / `DOMAINS` / `DOMAIN_SHAPES`
  / `config/data.json`, the per-provider tests, and the
  `tests/contract/test_provider_shapes.py` "live-only" branches.
- **Notes:** Largest single finding in the module.  `~1100 LOC` of
  registered-but-unused code plus matching test coverage.  Phase 3
  task labels in the comments suggest these were added speculatively
  ("Phase 3 — Finnhub earnings calendar / actuals").

## F-data-002
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** `src/data/providers/stats/yfinance.py:527-572`
  (`company_ratios.yfinance`) vs
  `src/data/providers/company_ratios/pit_composite.py:463-570`
  (`company_ratios.pit_composite`)
- **Evidence:** Two providers registered for the same domain.  Config
  selects `pit_composite`.  The `stats/yfinance.py` docstring at line
  546-548 says it is *"unsuitable for historical PIT queries — use
  pit_composite for backtests"*.  Live also runs through
  `pit_composite` (config flip is one-way).  No code path activates
  `company_ratios.yfinance` anywhere — `set_active_provider("company_ratios", "yfinance")`
  is never called in `src/` or `tests/` (grep returns nothing).
- **Intent violated:** "Provider switching must be one config flip"
  permits fallback shells, but a fallback that no one would flip to
  (intent docs explicitly say it's PIT-unsafe) is dead weight, not a
  shell.
- **Suggested action:** investigate — either keep as a documented
  live-degraded fallback (and remove the "unsuitable for backtests"
  language) or delete the second `@register` and its tests
  (`tests/unit/data/test_provider_registration.py:18-24`,
  `tests/unit/data/providers/test_stats_yfinance_as_of.py`).
- **Notes:** `stats/yfinance.py` itself is *not* dead: the
  `price_history` registration there is the active provider, and
  `src/orchestrator/tick.py:100` imports `_bulk_download` from it for
  the reference-price prefetch.  Only the second `@register` block is
  the dedupe candidate.

## F-data-003
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/data/providers/news/alpha_vantage.py` (757-line
  module; only `news.finnhub` is active and `news.tiingo` is
  documented as the legitimate backfill fallback)
- **Evidence:** `config/data.json` selects `finnhub`.
  `tests/unit/data/test_provider_switching.py` exercises a
  finnhub↔tiingo swap.  No test, scripts, or source code references
  `alpha_vantage` as a swap target.  `_news_alpha_vantage` import in
  `src/data/providers/__init__.py:7` is the only consumer.  AV is the
  only news provider that populates `NewsArticle.sentiment` (lines
  207-217), but per memory `news sentiment is intentionally null` —
  so the AV path's distinguishing feature is the one the system has
  decided not to use.
- **Intent violated:** memory: "news sentiment intentionally null".
- **Suggested action:** investigate — keep one of {tiingo, alpha_vantage}
  as the backfill fallback (tiingo is already exercised by the swap
  test) and delete the other.  Most likely outcome: delete
  `alpha_vantage.py` + `test_news_alpha_vantage_as_of.py` (757 + 757
  LOC).
- **Notes:** AV also adds a `relevance` field on `NewsArticle` (line
  227) — the only producer of that field; nothing reads it.

## F-data-004
- **Category:** silent-failure
- **Severity:** P0
- **Location:** `src/data/providers/social_sentiment/finnhub.py:79-85`
- **Evidence:**
  ```python
  except finnhub.FinnhubAPIException as exc:
      logger.warning("social_sentiment/finnhub: soft-fail for %s (%s)", symbol, exc)
      return SocialSentiment(ticker=symbol, snapshots=[], aggregate_score=0.0)
  ```
  Every API failure (auth, rate-limit 429, server error, premium-gate
  403) collapses to the exact same empty-but-valid `SocialSentiment`.
  Downstream `social_evidence` will see `is_no_data=False` (because
  the model parses) with zero signal, indistinguishable from "happy
  path returned no mentions".  Test-policy §A.7 explicitly bans this
  shape: "`is_no_data=True` fallbacks ... fail silently far more
  often than they raise".
- **Intent violated:** test-policy §A.7; memory "silent failures are
  the recurring bug class".
- **Suggested action:** investigate — at minimum, raise on 4xx
  excluding the documented 403-premium-gate; only the premium-gate
  path warrants soft-fail to empty.
- **Notes:** The active provider for `social_sentiment`.  The
  corresponding test
  (`tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py`,
  22 LOC) does **not** exercise the exception path (see F-data-013).

## F-data-005
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/data/providers/news/tiingo.py:147-150`,
  `src/data/providers/politician_trades/quiver.py:153-158`,
  `src/data/providers/politician_trades/fmp.py:251-258`
- **Evidence:** All three providers return `[]` when their API key
  env-var is unset:
  ```python
  api_key = os.getenv("TIINGO_API_KEY")
  if not api_key:
      logger.debug("TIINGO_API_KEY unset — fetch returning []")
      return []
  ```
  Missing config silently masquerades as "the upstream had no news /
  no politician trades for this ticker".  Contrast with
  `news/alpha_vantage.py:316` which uses `require_key()` and raises.
- **Intent violated:** test-policy §A.7; memory "silent failures are
  the recurring bug class".
- **Suggested action:** consolidate-with-secrets.require_key — make
  the absent-key path raise `SecretMissingError`.  If a provider is
  legitimately optional (e.g. backfill-only), gate it at config-load
  time rather than per-call.
- **Notes:** P1 not P0 because these are inactive/fallback providers
  in current config — but the moment someone flips `news` to `tiingo`
  in a new env without setting the key, the analyst would silently go
  blind.

## F-data-006
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/data/providers/news/finnhub.py:344-347` and
  `:355-359`,
  `src/data/providers/news/alpha_vantage.py:340-344`
- **Evidence:** Coerce failures and reversed windows both
  silently return `[]`:
  ```python
  if window_start is None or explicit_end is None:
      return []
  ...
  if window_start > window_end:
      return []
  ```
  The first ("caller passed something exotic") is a programmer error;
  the second is a caller-validation error.  Both deserve `ValueError`,
  not empty list.  Per the project's silent-failure pattern, a caller
  who passes a reversed window will get a green analyst run with
  zero news — and never know.
- **Intent violated:** test-policy §A.7.
- **Suggested action:** raise `ValueError` rather than return `[]`;
  leave the genuine "no articles in window" path returning `[]`.

## F-data-007
- **Category:** policy-mismatch
- **Severity:** P1
- **Location:** `src/data/registry.py:196-235` (`set_active_provider`)
- **Evidence:** The runtime swap helper accepts any string for
  `name`:
  ```python
  def set_active_provider(domain: str, name: str) -> Callable[[], None]:
      if domain not in DOMAINS:
          raise ValueError(f"unknown domain: {domain!r}")
      cfg = get_config()
      previous = cfg.providers[domain]
      cfg.providers[domain] = name   # no check that (domain, name) is registered
  ```
  The `_validate_active_providers_are_registered` check runs only at
  package import time (`src/data/__init__.py:40-57`).  A typo in
  `set_active_provider("news", "tiingp")` will succeed at swap time
  and only blow up at the first `dispatch("news", ...)` call with
  `RuntimeError`.
- **Intent violated:** §2.7 "A mismatch between `config/data.json`
  and a provider's `@register` decorator raises `RuntimeError` at
  import time" — same invariant should hold for runtime swaps.
- **Suggested action:** add `if (domain, name) not in _REGISTRY:
  raise` to `set_active_provider`.

## F-data-008
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/data/timeguard.py:155-159` and `:161-164`
- **Evidence:**
  ```python
  if strict or not allow_wallclock:
      raise AsOfRequiredError(...)
  _set_counter(_get_counter() + 1)
  return datetime.now(tz=UTC)
  ```
  In non-strict mode the wall-clock fallback fires silently and bumps
  a counter the live tick never reads — only the backtest driver
  drains it.  The live pipeline therefore has no observability on
  wall-clock fallback firings.  Not a backtest concern (strict mode
  catches it), but live ticks with a missing `as_of` upstream produce
  no log line.
- **Intent violated:** §2.7 "`as_of` is mandatory for backtest calls;
  optional/wall-clock in live" is honoured, but the counter
  infrastructure suggests the project wants telemetry — and live
  doesn't get any.
- **Suggested action:** investigate — either drop the counter (live
  doesn't use it) or emit a `WARNING` on each fallback so live logs
  show the leak.

## F-data-009
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/data/providers/__init__.py`,
  `src/data/providers/*/{__init__,*.py}`
- **Evidence:** Each domain dir has a 1- or 2-line `__init__.py`
  whose only job is to import the single (or two) provider modules
  underneath:
  ```
  src/data/providers/company_ratios/__init__.py:
      from . import pit_composite  # noqa: F401
  src/data/providers/options/__init__.py:
      (4-line docstring + zero imports — relies on
       providers/__init__.py importing options.yfinance directly)
  ```
  `src/data/providers/__init__.py` already imports every leaf module
  directly (`from .news import alpha_vantage as ...`), bypassing the
  per-domain `__init__.py`s.  The per-domain `__init__.py`s are
  therefore double-bookkeeping.
- **Suggested action:** delete the per-domain `__init__.py` files
  that only re-export, or remove the direct leaf imports in
  `providers/__init__.py` and rely on the per-domain ones.  Pick one.
- **Notes:** Cosmetic; current arrangement works.

## F-data-010
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/data/config.py:55`
  (`quiver_http_timeout_seconds`)
- **Evidence:** Only `data/providers/politician_trades/quiver.py:90`
  consumes it.  If F-data-001 lands (deleting `quiver` if `fmp`
  stays, or vice versa), this field will need to follow.  Even today
  it's named after the only consumer — which is itself a fallback
  provider — and the active provider (`fmp`) ignores it.
- **Suggested action:** investigate alongside the politician_trades
  dedupe.

## F-data-011
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/data/providers/politician_trades/fmp.py` vs
  `src/data/providers/politician_trades/quiver.py`
- **Evidence:** Both registered for `politician_trades`; config
  selects `fmp`; the wrapper `get_public_figure_trades` is called
  only by `src/agents/analysts/smart_money/fetch.py:109`.  The
  smart-money analyst itself emits no-data verdicts in the wired-but-
  empty mode (intent §7.1).  Memory note: backtest fetcher
  intentionally disables politician_trades entirely.  So in the live
  path one provider is active and the other is a "config flip"
  fallback that nothing tests beyond the swap regression.
- **Suggested action:** investigate — pick the canonical politician
  source and delete the other; both leaves are ~250 LOC.

## F-data-012
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/data/registry.py:101-116` vs
  `src/data/config.py:18-31`
- **Evidence:** Two parallel `_DOMAINS` / `DOMAINS` frozensets, with
  identical contents and identical comments warning they must stay
  in sync.  The duplication exists to avoid a circular import (per
  the comment at `config.py:14`).  Drift risk is real and explicit.
- **Suggested action:** investigate breaking the circular import (e.g.
  move the frozenset to a third module both `config` and `registry`
  import).

## F-data-013
- **Category:** test-gap
- **Severity:** P1
- **Location:** `tests/unit/data/providers/test_social_sentiment_finnhub_as_of.py`
  (22 LOC)
- **Evidence:** The whole file: only tests that `as_of` is accepted
  as a kwarg.  Does not exercise the `FinnhubAPIException` soft-fail
  branch (F-data-004), nor the happy-path payload shape, nor the
  aggregate_score calculation (which has a `max(mention_count, 1)`
  divide-by-zero guard whose behaviour with all-zero mentions is
  untested).  Per test-policy §A.7: the actual silent-failure path
  is untested.
- **Suggested action:** add tests that (a) raise from `_fetch_social`
  and assert the soft-fail SocialSentiment shape + warning log line
  fires, (b) seed a realistic payload and assert non-zero scores, (c)
  cover the zero-mentions division-by-zero guard.

## F-data-014
- **Category:** test-gap
- **Severity:** P1
- **Location:** `tests/unit/data/providers/test_news_tiingo.py:12-25`,
  `tests/unit/data/providers/test_politician_trades_quiver_as_of.py`,
  `tests/unit/data/providers/test_politician_trades_fmp.py`
- **Evidence:** Each provider's "missing API key" test asserts only
  `out == []`.  Per test-policy §A.7 ("Assert on positive output state,
  not just on completion") and the silent-failure pattern, these tests
  encode the bug as the behaviour — flipping the producer to `raise`
  would fail the test rather than fix the bug.
- **Suggested action:** when F-data-005 lands (raise on missing key),
  these tests change shape to `pytest.raises(SecretMissingError)`.

## F-data-015
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/unit/data/test_provider_registration.py:78-86`
  (`test_politician_trades_quiver_registers_on_import`),
  `tests/unit/data/providers/test_politician_trades_quiver_as_of.py`,
  `tests/unit/data/providers/test_news_tiingo.py`,
  `tests/unit/data/providers/test_news_alpha_vantage_as_of.py`
- **Evidence:** Tests for `quiver`, `tiingo`, `alpha_vantage`
  providers — none of which are the active provider; tiingo is the
  only one with an exercise-the-swap test
  (`test_provider_switching.py`), so the alpha_vantage and quiver
  registration tests verify only that import works.  When F-data-003
  and F-data-011 land these tests follow.
- **Suggested action:** delete or consolidate-with the dedupe pass.

## F-data-016
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/unit/data/providers/test_analyst_consensus_yfinance.py`
  (370 LOC),
  `tests/unit/data/providers/test_earnings_finnhub_as_of.py` (240
  LOC),
  `tests/unit/data/providers/test_short_interest_finra_as_of.py`
  (269 LOC),
  `tests/unit/data/providers/test_options_yfinance_shell.py` (95
  LOC)
- **Evidence:** Tests for providers with zero consumers (F-data-001).
  ~975 LOC of unit tests covering modules nothing else uses.
- **Suggested action:** delete alongside F-data-001.

## F-data-017
- **Category:** dedupe-candidate (test-consolidation)
- **Severity:** P2
- **Location:** `tests/unit/data/test_registry.py`,
  `tests/unit/data/test_registry_swap.py`,
  `tests/unit/data/test_provider_registration.py`,
  `tests/unit/data/test_provider_switching.py`,
  `tests/unit/data/test_dispatch_passes_as_of.py`,
  `tests/unit/data/test_active_pacing.py`
- **Evidence:** Six unit-test files all targeting `data.registry` /
  `data._dispatch` / `data.config` from slightly different angles.
  Considerable overlap — each one re-imports the providers and
  pokes at `_REGISTRY` / `_LIMITERS`.
- **Suggested action:** consolidate-with — merge into one
  `tests/unit/data/test_registry.py` covering registration, dispatch
  routing, swap round-trip, pacing, and `as_of` plumbing.

## F-data-018
- **Category:** policy-mismatch
- **Severity:** P3
- **Location:** `src/data/__init__.py:1` (`# ruff: noqa: E402`
  blanket disable)
- **Evidence:** The module-level `noqa: E402` exists because
  `_validate_active_providers_are_registered()` runs between
  imports.  Cosmetic, but worth a comment on each post-validation
  import block instead of a file-wide suppression.

## F-data-019
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/data/providers/filings/edgar.py:100,114,288`,
  `src/data/providers/notable_holders/edgar.py:279,334`,
  `src/data/providers/company_ratios/pit_composite.py:157`
- **Evidence:** Multiple bare `except Exception:` handlers in EDGAR
  + pit_composite paths.  Each one likely has a reason (per-row best-
  effort parsing), but a blanket `Exception` catch hides genuine
  programmer errors (typos, AttributeError in refactors) alongside
  the upstream-data weirdness they're meant to absorb.
- **Suggested action:** investigate — narrow each catch to the
  specific upstream exception type, or add a logged surface so the
  audit pass can see what's being swallowed.
- **Notes:** Lower severity because these are leaf data-mapping
  helpers, not the rate-limit / dispatch layer.

## F-data-020
- **Category:** dead-code
- **Severity:** P3
- **Location:** `src/data/models/news.py:25-27`
  (`NewsArticle.sentiment`)
- **Evidence:** Memory: "news sentiment intentionally null".  Only
  `news/alpha_vantage.py` populates the field; nothing reads it
  (`grep -rn "\.sentiment" src/agents/ src/contract/`).  If F-data-003
  lands (delete alpha_vantage), the field becomes write-never /
  read-never.
- **Suggested action:** investigate alongside F-data-003.

---

### Compactness check

The above 20 findings amount to roughly:

- **P0:** 1 (F-data-004)
- **P1:** 9 (F-data-001/002/003/005/006/007/013/014/015/016)
- **P2:** 8 (F-data-008/009/010/011/012/017/019)
- **P3:** 2 (F-data-018/020)

The largest single chunk of dead surface is **F-data-001 (the four
Phase 3 unused domains)** which carries F-data-016 (~975 LOC of
tests) on its back.
