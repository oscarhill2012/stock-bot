# Source audit — src/data/providers/

**Auditor:** subagent
**Date:** 2026-05-25
**Files audited:** 14 provider modules across 12 domains + 4 package `__init__.py` files
**Findings:** 0 P0 · 4 P1 · 4 P2 · 5 P3

## Summary

`src/data/providers/` houses the leaf HTTP-boundary modules that the `dispatch()` registry routes to per `config/data.json`. The audit found no contract violations (the `data/__init__.py` wrappers funnel all callers and `dispatch()` normalises arguments via `**kwargs`), but three of the four EDGAR-backed providers (`filings`, `insider_trades`, `notable_holders`) share the same `except Exception: continue` silent-failure pattern that quietly drops per-filing build failures — masking partial data loss as "no data". A second theme is hardcoded HTTP timeouts (`15.0` literals) sprinkled across five providers while `quiver.py` already shows the correct config-driven pattern via `get_config().quiver_http_timeout_seconds`. The cross-subsystem concern for consolidation: provider registration is split between explicit imports in `src/data/providers/__init__.py` and transitive imports in subpackage `__init__.py` files — currently complete (verified by runtime import), but the asymmetry is a one-bad-edit-away regression risk for the "one config flip" guarantee. Per user memory the fallback shells in `news/` and `politician_trades/` are intentional and are not flagged as dead.

## Findings

### P1-01 · C5 silent-failure attractor · EDGAR filings provider drops per-filing build failures without logging

- **Location:** `src/data/providers/filings/edgar.py:288`
- **Confidence:** high
- **Description:**
  Inside the form-iteration loop the per-filing branch has a bare `except Exception: continue` that silently discards any filing that fails to be turned into a `Filing` payload (parse error, attribute-access error, network glitch on lazy fetch, anything). The caller receives a shorter list and cannot tell whether (a) the issuer genuinely has fewer filings, (b) one form-type was rate-limited mid-page, or (c) the EDGAR shape changed and every filing now fails to parse. This is the canonical silent-degradation attractor called out in `test-policy §A.7` and in the `feedback_silent_failures_loud_tests` memory: empty/short results that look like "no data" but are actually "fetch failed".
- **Suggested action:**
  Log the exception with `ticker`, `form_type` and the filing accession at WARNING (or raise — let the orchestrator decide what is fatal). If the silent skip is genuinely desired for one specific upstream error, narrow the `except` to that class and assert positively in a test.

### P1-02 · C5 silent-failure attractor · EDGAR insider-trades provider swallows Form 4 parse failures twice

- **Location:** `src/data/providers/insider_trades/edgar.py:636-637` (`_fetch_and_parse_one` returns empty `Form4Bundle()`) and `src/data/providers/insider_trades/edgar.py:698` (main loop `except Exception: continue`)
- **Confidence:** high
- **Description:**
  Two stacked silent-failure attractors. `_fetch_and_parse_one` catches every exception from `filing.obj()` (the XBRL parse) and returns an empty `Form4Bundle()`; the caller cannot distinguish "issuer has no insider trades" from "EDGAR returned malformed XBRL". The outer loop then has its own `except Exception: continue` which discards any filing that survived `_fetch_and_parse_one` but blew up during downstream conversion. The Form 4 insider-trade signal is one of the noisier datasets to begin with, so a structural parse change at edgartools would degrade the signal to silently-empty without surfacing.
- **Suggested action:**
  Log at WARNING with the accession number on both branches, or — preferable per the "raise rather than null" principle in `feedback_silent_failures_loud_tests` — let the exception propagate and have the orchestrator decide. Add a test that injects a parse failure on a single filing and asserts the provider raises (or surfaces the error in the returned bundle).

### P1-03 · C5 silent-failure attractor · EDGAR notable-holders provider silently drops failed `_build` calls

- **Location:** `src/data/providers/notable_holders/edgar.py:334`
- **Confidence:** high
- **Description:**
  Same shape as P1-01 and P1-02. The 13F-iteration loop calls `_build(...)` for each holding row and wraps it in `except Exception: continue`. Per-holding build failures are discarded without any log line. Because 13F filings have hundreds of rows per filing, a partial decode failure produces a numerically-plausible but truncated holder list — the strategist would see "smaller holder concentration" rather than "the parse broke".
- **Suggested action:**
  Log at WARNING with the issuer CIK and the holding row index. Add a unit test that injects a malformed row and asserts both (a) the surfacing behaviour and (b) that the rest of the filing is still returned.

### P1-04 · C5 silent-failure attractor · Finnhub social-sentiment provider returns empty signal on premium-only 403

- **Location:** `src/data/providers/social_sentiment/finnhub.py:79-86`
- **Confidence:** high
- **Description:**
  When the Finnhub social-sentiment endpoint returns the premium-only error (`FinnhubAPIException`, normally a 403 because the free tier does not expose it), the provider silently returns `SocialSentiment(ticker=symbol, snapshots=[], aggregate_score=0.0)`. The strategist consumes a neutral signal indistinguishable from "this ticker is genuinely unmentioned on social". Per the project memory `project_news_sentiment_intentionally_null`, news sentiment is intentionally null because the upstream cannot provide it — but in that case the agent reads raw text and decides for itself. The social-sentiment path has no analogue and silently masks the free-tier inability behind a real-looking neutral score.
- **Suggested action:**
  Either (a) raise a typed `ProviderUnsupportedError` so the orchestrator can route around the absent signal, or (b) attach a flag to the model (e.g. `is_unavailable=True`) that the agent inspects. Document the chosen behaviour in `config/README.md` next to the `social_sentiment` provider entry. Add a contract test asserting the surfacing behaviour against a stubbed 403.

### P2-01 · C7 doc/code drift · Provider-registration strategy is split between parent and subpackage `__init__.py` files

- **Location:** `src/data/providers/__init__.py` (explicit imports for `news.alpha_vantage`, `news.finnhub`, `politician_trades.quiver`); `src/data/providers/news/__init__.py` (transitive import of `tiingo`); `src/data/providers/politician_trades/__init__.py` (transitive import of `fmp`)
- **Confidence:** high
- **Description:**
  Provider registration relies on import side-effects (the `@register` decorator runs at module load). Today, every domain has all of its providers registered (verified by `.venv/bin/python -c "import data.providers; from data.registry import _REGISTRY; print(sorted(_REGISTRY.keys()))"` — all 16 pairs present), but the strategy is inconsistent: some providers are imported by the parent `__init__.py`, others only by their subpackage `__init__.py`. The "one config flip" invariant (memory `feedback_provider_switching_must_be_one_line`) and the runtime `_validate_active_providers_are_registered()` check in `src/data/__init__.py` both depend on every provider module being imported transitively. A future contributor pruning what looks like an unused import in the parent `__init__.py` could silently break a fallback. Compounding this, `_validate_active_providers_are_registered` only checks *active* providers — so a broken fallback registration would not surface until someone flips the config switch.
- **Suggested action:**
  Pick one strategy and apply it uniformly. Either (a) drop all explicit imports from `src/data/providers/__init__.py` and let subpackage `__init__.py` files own registration (one place per domain), or (b) keep the explicit list at the parent level and remove the subpackage transitive imports. Document the chosen pattern at the top of `src/data/providers/__init__.py`. Optionally extend `_validate_active_providers_are_registered` to also assert that the full set of `_REGISTRY` keys matches the union of `(domain, provider)` pairs reachable from `DOMAINS × { live, cache, fallback names… }`.

### P2-02 · C7 doc/code drift · `as_of` parameter type splits between `date` and `datetime` across providers

- **Location:**
  - `date`: `src/data/providers/analyst_consensus/yfinance.py`, `src/data/providers/earnings/finnhub.py`, `src/data/providers/short_interest/finra.py`, `src/data/providers/options/yfinance.py`, `src/data/providers/news/alpha_vantage.py`
  - `datetime`: `src/data/providers/company_ratios/pit_composite.py`, `src/data/providers/insider_trades/edgar.py`, `src/data/providers/news/finnhub.py`, `src/data/providers/news/tiingo.py`, `src/data/providers/notable_holders/edgar.py`, `src/data/providers/politician_trades/fmp.py`, `src/data/providers/politician_trades/quiver.py`, `src/data/providers/social_sentiment/finnhub.py`, `src/data/providers/stats/yfinance.py`
  - Both/either: `src/data/providers/filings/edgar.py` (`as_of: date|datetime`)
- **Confidence:** high
- **Description:**
  The `data.get_*` wrappers all normalise `as_of` through `resolve_as_of(...)` which returns `datetime`, so callers are insulated — but leaf provider signatures disagree on whether they expect `date` or `datetime`. In several providers the `date`-annotated parameter receives a `datetime` from `dispatch()` and is implicitly relied on via duck-typing (`.year`, `.month` work on both). This is the "drift between providers" theme the user explicitly called out: when test-policy §G.3 stubs `_fetch_*` at the leaf, the type contract drifts away from the wrapper's contract. Not currently a bug, but exactly the shape of asymmetry that bites under refactoring (e.g. someone adds `.time()` to a `datetime`-annotated branch and breaks the `date`-annotated branches without warning).
- **Suggested action:**
  Standardise on one type — `datetime` is the more conservative choice because it is what `resolve_as_of` already returns. Update every leaf provider signature to `as_of: datetime` and coerce internally where a date is needed. Add a contract test (alongside `tests/contract/test_provider_shapes.py`) that introspects each registered provider's signature and asserts the `as_of` annotation matches.

### P2-03 · C7 doc/code drift · `options/yfinance.py` uses parameter name `symbol` instead of `ticker`

- **Location:** `src/data/providers/options/yfinance.py` (`async def fetch(symbol: str, *, as_of: date, **_)`)
- **Confidence:** high
- **Description:**
  Every other registered provider uses `ticker` as the first positional argument; the options-yfinance shell is the lone exception. `dispatch(domain, *args, **kwargs)` currently forwards positionally so the name asymmetry doesn't break — but the moment any caller tries `dispatch("options", ticker="AAPL", ...)` it will TypeError on the missing `symbol` argument. The wrapper for this domain (`get_options` or equivalent) does not currently exist in `src/data/__init__.py`, which is why this hasn't surfaced. This is dormant drift on a live-only shell.
- **Suggested action:**
  Rename the parameter to `ticker` for symmetry. While there, file an open question for the consolidator: should a `get_options(...)` wrapper exist in `src/data/__init__.py` for symmetry with the other twelve domains, or is the options domain currently unused (no agent reads it)?

### P2-04 · C2 parallel old/new branches · News providers have asymmetric `from_date`/`to_date` contracts

- **Location:**
  - `src/data/providers/news/finnhub.py` — `fetch(ticker, *, from_date, to_date, as_of, limit, **_unused)` (required)
  - `src/data/providers/news/tiingo.py` — `fetch(ticker, *, from_date, to_date, as_of, limit=50, **_unused)` (required)
  - `src/data/providers/news/alpha_vantage.py` — `fetch(ticker, *, as_of, lookback_days=7, from_date=None, to_date=None, **_)` (defaulted, derives window from `lookback_days`)
- **Confidence:** high
- **Description:**
  Three providers for one domain, two distinct contracts. The first two require an explicit window; the third can derive its own from `lookback_days`. The `data.get_stock_news` wrapper papers over this by always passing both — but the leaf-level asymmetry violates the "every registered provider shares one signature" guarantee from memory `feedback_provider_switching_must_be_one_line`. If a future caller stubs `_fetch_*` at the boundary (per test-policy §A.5), the test will read differently per provider for no semantic reason.
- **Suggested action:**
  Pick one contract. Recommendation: make `from_date`/`to_date` required across all three (matching `finnhub` and `tiingo`), and move the `lookback_days`-default derivation up into `data.get_stock_news` where the config is already read.

### P3-01 · C6 config-convention violation · Hardcoded `15.0`-second HTTP timeout in `earnings/finnhub.py`

- **Location:** `src/data/providers/earnings/finnhub.py:106` (`httpx.Timeout(15.0)`)
- **Confidence:** high
- **Description:**
  Inline literal timeout. `config/data.json` already exposes `quiver_http_timeout_seconds: 15.0` and `src/data/providers/politician_trades/quiver.py` shows the correct pattern of reading it via `get_config()`. The earnings provider hardcodes the same value.
- **Suggested action:**
  Add a `finnhub_http_timeout_seconds` (or shared `default_http_timeout_seconds`) key to `config/data.json`, document it in `config/README.md`, and read it via `get_config()`.

### P3-02 · C6 config-convention violation · Hardcoded `15.0`-second HTTP timeout in `news/tiingo.py`

- **Location:** `src/data/providers/news/tiingo.py:31` (`_HTTP_TIMEOUT = 15.0`)
- **Confidence:** high
- **Description:**
  Module-level constant, same magic number as P3-01.
- **Suggested action:**
  Same as P3-01 — source from `config/data.json`.

### P3-03 · C6 config-convention violation · Hardcoded `15.0`-second HTTP timeout in `news/alpha_vantage.py`

- **Location:** `src/data/providers/news/alpha_vantage.py:358` (`httpx.Timeout(15.0)`)
- **Confidence:** high
- **Description:**
  Inline literal, same magic number as P3-01 and P3-02.
- **Suggested action:**
  Same as P3-01.

### P3-04 · C6 config-convention violation · Hardcoded `15.0`-second HTTP timeout in `short_interest/finra.py`

- **Location:** `src/data/providers/short_interest/finra.py` (`_HTTP_TIMEOUT = 15.0`)
- **Confidence:** high
- **Description:**
  Module-level constant. Same pattern.
- **Suggested action:**
  Same as P3-01.

### P3-05 · C6 config-convention violation · Hardcoded `15.0`-second HTTP timeout in `politician_trades/fmp.py`

- **Location:** `src/data/providers/politician_trades/fmp.py:31` (`_HTTP_TIMEOUT = 15.0`)
- **Confidence:** high
- **Description:**
  `fmp.py` hardcodes its timeout while its sibling `quiver.py` in the same package reads `get_config().quiver_http_timeout_seconds`. The asymmetric sourcing within the same domain is the clearest case for tidying — the correct pattern is right next to the violation.
- **Suggested action:**
  Add `fmp_http_timeout_seconds` to `config/data.json` (document in `config/README.md`) and read via `get_config()`. Or, more cleanly, replace the per-upstream timeout keys with a single `default_http_timeout_seconds` and let each provider override only when it needs to differ.
