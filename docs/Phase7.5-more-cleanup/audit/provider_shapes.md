# Phase 7.6 — Provider shape audit

Recorded: 2026-05-18.  Source for `DOMAIN_SHAPES` in Task 2.

---

## Provider domains

Notes on the table columns:

- **Live entry-point** — the file containing the `@register`-decorated function and
  the function name (`fetch` unless otherwise noted).
- **Cache entry-point** — the file in `src/backtest/providers/`; all cache entry
  points are named `fetch`.
- **Live return type** — the declared `-> ...` annotation verbatim from the source.
- **Cache return type** — same, verbatim from the cache file.
- **Match?** — `✓` when both annotations resolve to the same canonical shape;
  `✗` when they diverge.
- **Canonical shape** — the chosen `DomainShape` (`container / payload_type`) per
  the spec's picking principle (single → one model; list → many of same; bundle →
  genuinely multiple distinct sublists).
- **Drift fix needed** — which side (live / cache / both) must change to reach the
  canonical shape, or `none` when already aligned.

| Domain | Live entry-point | Cache entry-point | Live return type | Cache return type | Match? | Canonical shape | Drift fix needed |
|---|---|---|---|---|---|---|---|
| `price_history` | `src/data/providers/stats/yfinance.py` · `fetch_price_history` | `src/backtest/providers/price_history_cache.py` · `fetch` | `PriceHistory` | `PriceHistory` | ✓ | `single / PriceHistory` | none |
| `company_ratios` | `src/data/providers/stats/yfinance.py` · `fetch_company_ratios` (yfinance) or `src/data/providers/company_ratios/pit_composite.py` · `fetch` (pit_composite) | `src/backtest/providers/company_ratios_cache.py` · `fetch` | `CompanyRatios` | `CompanyRatios \| None` | ✗ | `single / CompanyRatios` | cache (drop the `\| None` — return a sentinel or raise instead) |
| `news` | `src/data/providers/news/alpha_vantage.py`, `finnhub.py`, or `tiingo.py` · `fetch` | `src/backtest/providers/news_cache.py` · `fetch` | `list[NewsArticle]` | `list[NewsArticle]` | ✓ | `list / NewsArticle` | none |
| `social_sentiment` | `src/data/providers/social_sentiment/finnhub.py` · `fetch` | `src/backtest/providers/social_sentiment_cache.py` · `fetch` | `SocialSentiment` | `None` | ✗ | `single / SocialSentiment` | cache (v1 stub deliberately returns `None`; canonical is `SocialSentiment` — Phase B must decide: align cache to return an empty `SocialSentiment`, or mark as live-only until backlog B19 lands) |
| `insider_trades` | `src/data/providers/insider_trades/edgar.py` · `fetch` | `src/backtest/providers/insider_trades_cache.py` · `fetch` | `Form4Bundle` | `Form4Bundle` | ✓ | `bundle / Form4Bundle` | none |
| `politician_trades` | `src/data/providers/politician_trades/fmp.py` or `quiver.py` · `fetch` | `src/backtest/providers/politician_trades_cache.py` · `fetch` | `list[PoliticianTrade]` | `list[PoliticianTrade]` | ✓ | `list / PoliticianTrade` | none |
| `notable_holders` | `src/data/providers/notable_holders/edgar.py` · `fetch` | `src/backtest/providers/notable_holders_cache.py` · `fetch` | `list[NotableHolder]` | `list[NotableHolder]` | ✓ | `list / NotableHolder` | none |
| `filings` | `src/data/providers/filings/edgar.py` · `fetch` | `src/backtest/providers/filings_cache.py` · `fetch` | `list[Filing]` | `list[Filing]` | ✓ | `list / Filing` | none |
| `earnings` | `src/data/providers/earnings/finnhub.py` · `fetch` | live-only (no cache provider) | `EarningsHistory` | — | n/a | `single / EarningsHistory` | none (live-only domain; cache provider TBD) |
| `analyst_consensus` | `src/data/providers/analyst_consensus/yfinance.py` · `fetch` | live-only (no cache provider) | `tuple[AnalystRating, list[AnalystRevision]]` | — | n/a | `bundle / AnalystConsensusBundle` | live (no `AnalystConsensusBundle` wrapper model exists yet — must be created in Task 5 before live return can be aligned; see note below) |
| `short_interest` | `src/data/providers/short_interest/finra.py` · `fetch` | live-only (no cache provider) | `list[ShortInterestSnapshot]` | — | n/a | `list / ShortInterestSnapshot` | none (live-only domain; cache provider TBD) |
| `options` | `src/data/providers/options/yfinance.py` · `fetch` | live-only (no cache provider) | `dict[str, Any]` | — | n/a | — (shell only; no model yet) | live (shell returns `dict[str, Any]` or `{}`; no `OptionContract` model exists — canonical shape deferred to when the real implementation lands; mark as `# TODO: confirm type in Task 12`) |

---

## Domain notes

### `price_history`

`PriceHistory` is a bundle-shaped wrapper (contains `ticker: str` and
`bars: list[OHLCBar]`) but has only one natural payload — it wraps a single
ticker's OHLCV series.  The spec's principle applies: one thing → `single`.
Container is `single / PriceHistory`, not `list / OHLCBar`, because the
provider always returns the full history object, not a bare list.

### `company_ratios`

Two live providers are registered for this domain: `yfinance` (in
`src/data/providers/stats/yfinance.py`) and `pit_composite` (in
`src/data/providers/company_ratios/pit_composite.py`).  Both declare `->
CompanyRatios`.  The cache provider declares `-> CompanyRatios | None`; the
`None` branch is returned when the store has no snapshot before `as_of`.
Canonical shape is `single / CompanyRatios`.  The cache-side drift fix is to
either raise a domain-specific exception (preferred — consistent with how
other domains handle missing cache data) or return an empty/sentinel
`CompanyRatios` rather than `None`.

### `social_sentiment`

The cache provider (`social_sentiment_cache.py`) is a v1 stub that
unconditionally returns `None` (backlog item B19).  The live provider returns
`SocialSentiment`.  Two resolution paths exist for Task 16:

1. **Align now:** cache returns `SocialSentiment(ticker=ticker, snapshots=[],
   aggregate_score=0.0)` — a well-typed empty value rather than `None`.
   Downstream code already checks for `None` / empty; an empty model is
   structurally identical at runtime.
2. **Treat as live-only until B19:** mark `social_sentiment` in `_LIVE_ONLY`
   alongside the other four, and skip the cache half of the contract test.

The task engineer must choose one path in Task 16.  Recommendation: option 1
— it is a one-line change that closes the gap without deferring more work.

### `earnings`

`EarningsHistory` follows the `<History>` / `<Bundle>` wrapper pattern
(contains `ticker: str` and `reports: list[EarningsReport]`).  Like
`PriceHistory`, one thing → `single / EarningsHistory`.

### `analyst_consensus`

Live provider returns `tuple[AnalystRating, list[AnalystRevision]]` — two
genuinely distinct sublists with no natural single payload type.  This is the
"multiple distinct sublists" case → `bundle`.  However, **no
`AnalystConsensusBundle` wrapper model currently exists** in
`src/data/models/`.  Task 5 must:

1. Create `src/data/models/analyst_consensus.py` addition:
   `AnalystConsensusBundle(rating: AnalystRating, revisions:
   list[AnalystRevision])`.
2. Update the live `fetch` return annotation and return statement to wrap the
   tuple in `AnalystConsensusBundle(rating=..., revisions=...)`.

`AnalystRating` and `AnalystRevision` already exist in
`src/data/models/analyst_consensus.py`.

### `short_interest`

Live provider returns `list[ShortInterestSnapshot]` — a time-series of
observations, not a single snapshot.  Canonical: `list / ShortInterestSnapshot`.

### `options`

The live provider is an explicit shell: it returns `{}` immediately for all
`as_of` values, with a note that real wiring is deferred to a follow-up spec.
The return annotation is `dict[str, Any]`.  No `OptionContract` model exists.
**Do not assign a canonical shape at this point.**  In `DOMAIN_SHAPES`, add a
placeholder entry with a `# TODO: confirm type in Task 12` comment.  Task 12
either defines the model and aligns the provider, or confirms the domain stays
out of `DOMAIN_SHAPES` until the shell is replaced.

---

## Cache-provider inventory

Verified against `src/backtest/providers/`:

| Domain | Cache provider file | Exists? |
|---|---|---|
| `price_history` | `price_history_cache.py` | ✓ |
| `company_ratios` | `company_ratios_cache.py` | ✓ |
| `news` | `news_cache.py` | ✓ |
| `social_sentiment` | `social_sentiment_cache.py` | ✓ (v1 stub — returns `None`) |
| `insider_trades` | `insider_trades_cache.py` | ✓ |
| `politician_trades` | `politician_trades_cache.py` | ✓ |
| `notable_holders` | `notable_holders_cache.py` | ✓ |
| `filings` | `filings_cache.py` | ✓ |
| `earnings` | — | ✗ (live-only) |
| `analyst_consensus` | — | ✗ (live-only) |
| `short_interest` | — | ✗ (live-only) |
| `options` | — | ✗ (live-only / shell) |

---

## Orphaned aggregator tests

These files exercise `get_stock_signal_bundle` or `StockSignalBundle` and have
no production callers.  They are the deletion targets for Phase D (Task 18).

- `tests/unit/data/test_aggregator.py` — 2 test functions
  (`test_bundle_returns_stock_signal_bundle`,
  `test_bundle_captures_provider_failure`); the whole file exercises the
  aggregator.
- `tests/unit/data/models/test_bundle.py` — exercises `StockSignalBundle`
  directly (construction, serialisation, round-trip).
- `tests/unit/data/test_as_of_threading.py` — contains a test that imports
  `get_stock_signal_bundle` from `data.aggregator` to verify `as_of`
  threading; the aggregator reference is the only reason this file is listed.

**Note on `tests/contract/test_lookbacks_sourced_from_config.py`:** this file
contains a comment mentioning `get_stock_signal_bundle` but explicitly states
"The aggregator (`get_stock_signal_bundle`) is deliberately *not* tested here —
Phase 7.6 deletes the function entirely."  No import or call is present; the
file is **not** a deletion target.

---

## Smart-money slicing sites

All sites reading the old category-first `state["smart_money_data"]` shape.
These are the targets for the fetch-site and agent-side rewrites in Task 17.

### Write sites (fetch callback — `src/agents/analysts/smart_money/fetch.py`)

- `fetch.py:110` — `smart_money_data["politicians"][ticker] = [...]`
  (writes serialised politician-trade dicts into the category-first outer dict)
- `fetch.py:113` — `smart_money_data["notable_holders"][ticker] = [...]`
  (writes serialised holder dicts into the category-first outer dict)

The surrounding block (approximately lines 95–118) initialises the category-first
structure and assigns it to `state["smart_money_data"]`.  The full block is the
reshape target.

### Read sites (agent body — `src/agents/analysts/smart_money/agent.py`)

- `agent.py:110` — `data: dict[str, dict] = state.get("smart_money_data", {}) or {}`
  (reads the outer dict)
- `agent.py:124` — `politicians_by_ticker = data.get("politicians", {})`
  (category-first slice)
- `agent.py:125` — `notable_holders_by_ticker = data.get("notable_holders", {})`
  (category-first slice)
- `agent.py:133–136` — per-ticker slice via
  `politicians_by_ticker.get(ticker, [])` and
  `notable_holders_by_ticker.get(ticker, [])`

All four agent-body sites must be updated together with the fetch-site rewrite
to maintain internal consistency (see spec §5 and plan Task 17).
