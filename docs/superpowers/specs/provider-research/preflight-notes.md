# Preflight notes — Group A verification

Generated from `scripts/api_smoke.py` on 2026-05-17.

## A1. Alpha Vantage NEWS_SENTIMENT — SVB-window archive depth

- Probe: `probe_alpha_vantage_news` (AAPL; originally SVB window 20230306T0000 → 20230313T0000, extended in §A6 to multi-window Jan / Jun / Dec 2023).
- Result: OK — 9 articles returned for the SVB window, earliest `time_published=20230307T021527`.
- Verdict: archive depth covers SVB.  Phase 3 alpha_vantage news provider is unblocked.

## A2. FINRA short-interest endpoint path

- Probe: `probe_finra_short_interest`
- Token URL: `https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token?grant_type=client_credentials` (Basic auth with client id / secret).
- Data URL: `https://api.finra.org/data/group/otcMarket/name/regShoDaily` — **must send `Accept: application/json`** or the endpoint returns CSV by default.
- Response shape: top-level JSON array (not wrapped in `{"data": [...]}`).  Fields: `tradeReportDate`, `securitiesInformationProcessorSymbolIdentifier`, `shortParQuantity`, `shortExemptParQuantity`, `totalParQuantity`, `marketCode`, `reportingFacilityCode`.
- Sibling candidate `shortInterestExch` returns 404 — dataset name does not exist.  Single canonical endpoint is `regShoDaily`.

## A3. StockTwits free-tier limits

- Probe: `probe_stocktwits` (AAPL stream)
- Result: OK — 30 messages returned.  **`X-RateLimit-Remaining` header is not exposed** in the response (probe printed `?`); the public-stream endpoint does not advertise its budget.
- Operational implication: assume StockTwits docs' published 200 req/hour for unauthenticated callers; throttle conservatively.
- **Gotcha (recorded for the live-implementation plan):** the endpoint sits behind Cloudflare and responds 403 to the default `python-httpx/<ver>` User-Agent.  Any future provider MUST send a browser-shaped UA (`Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 ...`).
- **Decision:** drop Row #13 (social sentiment / StockTwits) from v1.  StockTwits exposes no historical archive on the free tier — the provider would need a 30-day forward-cache warm-up before producing a useful baseline, and StockBot is pre-deployment so there is no live clock to accumulate that window against.  `config/data.json` keeps `social_sentiment: "finnhub"` (the existing soft-failing provider); Social analyst lands as `is_no_data=True` throughout v1 per spec decision 9.3 (Social is the one analyst explicitly allowed to soft-fail); the no-silent-zero-features test in Phase 7 already exempts Social.  Re-open Row #13 in the live-implementation plan once the 30-day window can run in real time.

## A4. Senate & House Stock Watcher repo coverage

- Probe: `probe_stock_watcher`
- Result: **FAIL — dataset is effectively dead.**
  - `senate-stock-watcher-data.s3-us-west-2.amazonaws.com` → 403 AccessDenied (bucket policy removed).
  - `house-stock-watcher-data.s3-us-west-2.amazonaws.com` → 403 AccessDenied.
  - `senatestockwatcher.com` and `housestockwatcher.com` → DNS does not resolve.
  - `https://raw.githubusercontent.com/timothycarambat/senate-stock-watcher-data/master/aggregate/all_transactions.json` → 200 OK but repo last pushed 2021-03-16 (stale).
  - No live House mirror exists under any active GitHub owner (verified via `gh api repos/search` + targeted lookups).
- **Decision:** drop Row #14 (politician trades) from v1.  Quiver provider stays in-place soft-failing to `[]`.  Re-introduce in a follow-up plan when a credible free source surfaces.

## A5. FMP `/senate-disclosure` chamber semantics

- Not investigated.  Row #14 dropped from v1 (see A4), so chamber semantics are not on the critical path.  Re-open in the follow-up plan if FMP becomes the chosen politician-trades source.

## Additional probe findings worth recording

- `probe_edgartools_8k`: `filing.items` returns the items list as a **comma-delimited string** (e.g. `"2.02,9.01"`), not a Python list.  Iterating it character-by-character yields `['2', '.', '0', '2', ',', '9', '.', '0', '1']`.  Phase 4 Task 4.1 must `.split(",")` to extract items.
- `probe_finnhub_earnings`: `earningsCalendar` keys observed: `date`, `epsActual`, `epsEstimate`, `revenueActual`, `revenueEstimate`, `symbol`, `year`, `quarter`, `hour`.  No `guidance_text` field — must source from EDGAR 8-K Item 2.02 prose.
- `probe_yfinance_analyst`: `.analyst_price_targets` returns a `dict` with keys `current, high, low, mean, median` (no per-firm breakdown).  For analyst counts use `Ticker.info["numberOfAnalystOpinions"]`.
- `probe_yfinance_bulk`: `yf.download(["SPY","XLK"], period="5d", auto_adjust=False)` returns the expected multi-ticker DataFrame.  Phase 5 reference_prices plumbing works without further investigation.

## A6. Phase -1 verification pass (2026-05-17)

Ran via a one-off `/tmp/verify_apis.py` script (now folded permanently into `scripts/api_smoke.py` per Step 0 above).  Findings — all four are baked into this plan revision:

- **AV NEWS_SENTIMENT archive depth** — 2023-01-10→17, 2023-06-10→17, 2023-12-10→17 returned 21 / 25 / 9 articles respectively with NO `Information` or `Note` rate-limit messaging.  Archive depth confirmed across the full 2023 calendar.  **Budget reality**: free tier 25 req/day means 50 tickers × 1 call/day exceeds budget by 2× — Task 3.2 documents this as expected; backtest fill is a staggered multi-day operation; live news provider TBD via config swap (per project memory on provider switching).
- **regShoDaily field shape** — confirmed live response fields are `{marketCode, reportingFacilityCode, securitiesInformationProcessorSymbolIdentifier, shortExemptParQuantity, shortParQuantity, totalParQuantity, tradeReportDate}`.  **None of these match the plan's `ShortInterestSnapshot(ticker, settlement_date, short_interest, days_to_cover, report_publish_date)` model directly** — Task 3.3 has been re-scoped to make the synthesis path (outcome b) primary.  Per-date gotcha: AAPL on a single date returned 3 rows (different `marketCode` venues — `B`, plus two others); the synthesis must sum-within-day before sum-across-days, otherwise short volume is under-counted by ~3×.
- **Finnhub earnings PIT model** — queried `from=today, to=today+90d` for AAPL; response included one row (AAPL 2026-07-29 Q3) with `epsActual=null, revenueActual=null`.  Confirms the API does NOT filter unannounced future events even when the date is in the future window.  **Provider design requirement**: Task 3.1 applies a dual filter (`date <= as_of` AND `epsActual is not None`); otherwise the bot would pre-announce earnings during backtest (and during live runtime as well, since the same filter logic applies).
- **No separate `disclosure_date` field** in Finnhub earnings response — the `date` field IS the announcement date.  Backtest fidelity caveat: agents see earnings the morning after release at the earliest (the `as_of` granularity is per-day in the tick schedule), which matches real-world dissemination latency closely enough for v1.
