# Providers & Silent Gaps v1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** [`docs/Phase7-pre-backtest-cleanup/providers-and-silent-gaps-spec.md`](./providers-and-silent-gaps-spec.md)
**Brief:** [`docs/data-and-providers.md`](../data-and-providers.md)
**Audit:** [`docs/superpowers/specs/provider-research/free-wins-audit.md`](../superpowers/specs/provider-research/free-wins-audit.md)

**Goal:** Land 11 silent-gap extractor fixes, 5 new provider shells, 5 existing-provider extensions, and `state["reference_prices"]` plumbing — bundled into one PR that unblocks a meaningful SVB-stress 2023-03 backtest.

**Architecture:** Additive only. No new abstractions; existing `Provider` protocol unchanged. Model files grow optional fields (default `None`); extractor files swap dict-flattening for typed-object reads; new providers register via the existing `@register` decorator; one new pre-tick populator seeds `state["reference_prices"]` from a single bulk yfinance call.

**Tech Stack:** Python 3.11, Pydantic v2, pytest (`-m "not slow and not integration"` default), edgartools (SEC), yfinance (Yahoo), Finnhub SDK, Alpha Vantage REST, FINRA OAuth2 REST, requests, asyncio.

**PR strategy:** Single feature branch `providers-and-silent-gaps-v1`. Seven commits, one per phase. `pytest -m "not slow and not integration" -q` must be green between phases.

**Scope note (re-scoped 2026-05-17 from smoke-test findings + scoping decision):**

1. **Row #14 (politician trades) and Stock Watcher are dropped from v1.** The S3 buckets `senate-stock-watcher-data.s3-us-west-2.amazonaws.com` and `house-stock-watcher-data.s3-us-west-2.amazonaws.com` now return 403; `senatestockwatcher.com` / `housestockwatcher.com` no longer resolve; the only live mirror (`timothycarambat/senate-stock-watcher-data` on GitHub) was last pushed in 2021. The existing `politician_trades/quiver.py` provider remains in the repo and continues to soft-fail to `[]` when `QUIVER_QUANT_API_KEY` is unset — SmartMoney loses its politicians signal but keeps notable_holders (EDGAR 13D/13G/13F), and insider trades stay intact on the Fundamental analyst (EDGAR Form 4). Re-introduce politician trades in a follow-up plan if a credible free source emerges.

2. **StockTwits (Row #13 / social sentiment) is dropped from v1.** StockTwits exposes no historical archive on the free tier — the provider would need a 30-day forward-cache warm-up before producing useful baseline signal. Since StockBot is pre-deployment (no paper or live instance running) and the team is not waiting 30 days to run backtests, building the shell now is premature.  `config/data.json` keeps `social_sentiment: "finnhub"` (the existing soft-failing provider), the Social analyst lands as `is_no_data=True` throughout v1 per spec decision 9.3 (Social is the one analyst explicitly allowed to soft-fail), and the no-silent-zero-features test in Phase 7 already exempts Social.  When the live pipeline starts and the 30-day warm-up window can run in real time, re-open Row #13 in the live-implementation plan.

3. **Phase -1 verification pass (2026-05-17) — APIs confirmed, four plan amendments folded in.**  Before opening Phase 0, a one-off `/tmp/verify_apis.py` probe re-hit Alpha Vantage NEWS_SENTIMENT across three 2023 windows (Jan/Jun/Dec — all returned data with no rate-limit messaging), dumped the full `regShoDaily` field shape (confirms it is daily short-sale **volume**, not biweekly snapshot — plan synthesises the snapshot from `shortParQuantity` aggregates), and probed Finnhub `/calendar/earnings` with a `to` date 90 days in the future (response includes the upcoming AAPL Q3 report with `epsActual=null`, proving the API does not auto-filter unannounced events).  The findings drove **four structural amendments** baked into this plan revision: Task 0.1 smoke probes extended (P-1.2/3/4 added permanently); Task 2.11 keeps the existing extractor signature (state-passing was an incidental rewrite, not the goal); Task 3.1 adds a dual PIT filter (`date <= as_of` AND `epsActual is not None`); Task 3.3 promotes the synthesis path (outcome b) to primary with explicit per-date row aggregation.  **Two further concerns are documented as known and intentionally accepted**: Alpha Vantage's 25/day free-tier cap means a multi-day staggered cache fill is required (Task 3.2 framing — "backtest-fill provider; live news provider TBD via `config/data.json` swap per project memory on provider switching"), and `notable_holders` body-parse doubles per-filing EDGAR roundtrips (Task 4.3 framing — acceptable for a one-shot fill, live runtime only touches current-month filings).  The full Phase -1 verification report is captured inline in Task 0.1 §A6.

---

## File Structure

**New files (10):**
- `src/data/models/earnings.py` — `EarningsReport`, `EarningsHistory`
- `src/data/models/analyst_consensus.py` — `AnalystRating`, `AnalystRevision`
- `src/data/models/short_interest.py` — `ShortInterestSnapshot`
- `src/contract/extractors/_sector_map.py` — `SECTOR_TO_ETF` constant
- `src/data/providers/earnings/__init__.py` + `finnhub.py`
- `src/data/providers/news/alpha_vantage.py`
- `src/data/providers/short_interest/__init__.py` + `finra.py`
- `src/data/providers/analyst_consensus/__init__.py` + `yfinance.py`
- `src/data/providers/options/__init__.py` + `yfinance.py`
- `tests/integration/backtest/test_no_silent_zero_features.py`
- Plus unit tests under `tests/unit/data/providers/` and `tests/unit/contract/extractors/` per provider/extractor touched.

**Modified files (≈19):**
- `src/data/models/`: `company_ratios.py`, `filings.py`, `trades.py`, `sentiment.py`, `news.py`, `bundle.py`
- `src/data/registry.py` (add 4 domains)
- `src/data/providers/__init__.py` (auto-import new modules)
- `src/data/providers/`: `filings/edgar.py`, `insider_trades/edgar.py`, `notable_holders/edgar.py`, `company_ratios/pit_composite.py`, `politician_trades/quiver.py`, `stats/yfinance.py`
- `src/contract/extractors/`: `technical.py`, `fundamental.py`, `news.py`, `social.py`, `smart_money.py`
- `src/agents/analysts/social/fetch.py` (stop flattening)
- `src/orchestrator/tick.py` (`_build_initial_state` + reference-price populator)
- `config/data.json`, `config/README.md` (new provider keys; no Stock Watcher or StockTwits entries)
- `tests/integration/backtest/test_end_to_end_smoke.py` (manifest assertion)

---

## Phase 0 — Preflight verification (Group A items)

**Already executed.** The smoke script `scripts/api_smoke.py` (committed at the top of this branch) ran the equivalent of the Group A probes in a single pass on 2026-05-17. Phase 0's only remaining work is to capture those findings as a written note so subsequent phases can reference them, then commit.

### Task 0.1: Write up smoke-script findings

**Files:**
- Modify: `scripts/api_smoke.py` (extend three existing probes with the deeper Phase -1 checks)
- Create: `docs/superpowers/specs/provider-research/preflight-notes.md` (new — append-only log)

- [ ] **Step 0: Extend the three Phase -1-verified probes**

The Phase -1 verification pass (2026-05-17) ran one-off deeper probes that must be folded into `scripts/api_smoke.py` permanently so regressions surface at smoke-test time, not at backtest-fill time.  Extend the three relevant probe functions in-place — do **not** add new top-level probes, the existing 8-probe inventory stays constant in shape.

**(a) `probe_alpha_vantage_news` (line 148)** — replace the single-window call with a list comprehension over three 2023 windows (Jan/Jun/Dec) and report the article count per window plus any `Information` / `Note` field from the response.  Pass if all three windows return ≥1 article and no rate-limit messaging; fail otherwise:

```python
def probe_alpha_vantage_news(env: dict[str, str]) -> ProbeResult:
    """AV NEWS_SENTIMENT — verify archive depth across the full 2023 calendar
    (multi-window covers seasonal coverage gaps).  Free tier 25 req/day — this
    probe uses 3 of that budget per smoke run.
    """
    key = env.get("ALPHA_VANTAGE_API_KEY")
    if not key:
        return ProbeResult("alpha-vantage-news", "SKIP", "no key")
    windows = [("20230110T0000", "20230117T2359"),
               ("20230610T0000", "20230617T2359"),
               ("20231210T0000", "20231217T2359")]
    counts = []
    notices = []
    for ts_from, ts_to in windows:
        params = {"function": "NEWS_SENTIMENT", "tickers": "AAPL",
                  "time_from": ts_from, "time_to": ts_to,
                  "limit": 50, "apikey": key}
        # ... existing GET pattern ...
        body = ...  # JSON dict
        counts.append(len(body.get("feed") or []))
        if body.get("Information") or body.get("Note"):
            notices.append(body.get("Information") or body.get("Note"))
    if any(c == 0 for c in counts) or notices:
        return ProbeResult("alpha-vantage-news", "FAIL",
                           f"counts={counts} notices={notices}")
    return ProbeResult("alpha-vantage-news", "OK",
                       f"per-window articles: {counts}")
```

**(b) `probe_finra_short_interest` (line 190)** — extend the field-dump branch to explicitly list every field name in the first row and assert the set matches the seven Phase -1 confirmed fields (`marketCode, reportingFacilityCode, securitiesInformationProcessorSymbolIdentifier, shortExemptParQuantity, shortParQuantity, totalParQuantity, tradeReportDate`).  Also assert the response is a top-level list (not dict-wrapped):

```python
# Inside probe_finra_short_interest, after parsing the JSON response:
EXPECTED_FIELDS = {"marketCode", "reportingFacilityCode",
                   "securitiesInformationProcessorSymbolIdentifier",
                   "shortExemptParQuantity", "shortParQuantity",
                   "totalParQuantity", "tradeReportDate"}
if not isinstance(rows, list):
    return ProbeResult("finra-short-interest", "FAIL",
                       "response is not a top-level array")
if rows:
    got = set(rows[0].keys())
    missing = EXPECTED_FIELDS - got
    extra   = got - EXPECTED_FIELDS
    if missing:
        return ProbeResult("finra-short-interest", "FAIL",
                           f"missing fields: {missing}")
    # `extra` is informational — FINRA may add fields; don't fail on them.
```

**(c) `probe_finnhub_earnings` (line 115)** — extend the single recent-120-day window with a second call using `from=today, to=today+90d` for AAPL.  Assert the response includes at least one row with `epsActual=null` (proves the API does NOT auto-filter unannounced future events — the provider in Task 3.1 must apply the dual filter):

```python
# After the existing recent-history probe, append:
future_params = {"symbol": "AAPL", "from": today.isoformat(),
                 "to": (today + timedelta(days=90)).isoformat(),
                 "token": key}
# ... GET /calendar/earnings ...
future_cal = future_body.get("earningsCalendar") or []
unannounced = [r for r in future_cal if r.get("epsActual") in (None, "")]
if not unannounced:
    return ProbeResult("finnhub-earnings", "FAIL",
                       "no unannounced future rows in 90d window — "
                       "API behaviour may have changed; re-verify Task 3.1 PIT filter")
```

- [ ] **Step 1: Run the smoke script once and capture the output**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.api_smoke | tee /tmp/api_smoke.txt
```

Expected: 7 OK / 0 SKIP / 1 FAIL (the FAIL is `stock-watcher` — see scope note in the plan header; not a blocker).  The three extended probes (`alpha-vantage-news`, `finra-short-interest`, `finnhub-earnings`) now embed the Phase -1 verification checks; if any of them flips to FAIL, the corresponding Phase 3 task must be re-verified before code lands.  If any other probe fails, stop and fix the probe + credentials before continuing.

- [ ] **Step 2: Write the preflight notes file**

Create `docs/superpowers/specs/provider-research/preflight-notes.md` with these sections (one per spec §11 Group A item):

````markdown
# Preflight notes — Group A verification

Generated from `scripts/api_smoke.py` on 2026-05-17.

## A1. Alpha Vantage NEWS_SENTIMENT — SVB-window archive depth

- Probe: `probe_alpha_vantage_news` (AAPL, 20230306T0000 → 20230313T0000)
- Result: OK — 9 articles returned, earliest `time_published=20230307T021527`.
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
````

- [ ] **Step 3: Commit preflight notes**

```bash
git checkout -b providers-and-silent-gaps-v1
git add scripts/api_smoke.py docs/superpowers/specs/provider-research/preflight-notes.md
git commit -m "docs(preflight): capture api_smoke.py findings for Group A gating

Records empirical results for spec §11 items A1-A5 plus four
provider-specific gotchas (FINRA Accept header, StockTwits User-Agent,
edgartools filing.items CSV string, Finnhub guidance gap), and folds
the Phase -1 verification probes (multi-window AV NEWS_SENTIMENT,
regShoDaily field-shape assertion, Finnhub future-event PIT check)
permanently into api_smoke.py.  Drops Row #14 (Stock Watcher dead,
Quiver stays soft-failing) and Row #13 (StockTwits needs 30d warm-up —
deferred to live-implementation plan) from v1."
```

---

## Phase 1 — Model extensions

Six existing model files grow optional fields; three new model files; `StockSignalBundle` extended. All fields default to `None` / empty for back-compat. **TDD discipline:** every new field gets a unit test that round-trips the model with the field populated and with the field absent.

### Task 1.1: Extend `CompanyRatios` with 10 fields

**Files:**
- Modify: `src/data/models/company_ratios.py`
- Test: `tests/unit/data/models/test_company_ratios.py` (new if missing)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/data/models/test_company_ratios.py
from data.models.company_ratios import CompanyRatios
from datetime import date

def test_company_ratios_accepts_new_fields():
    r = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        peg=1.8, revenue_growth_yoy=0.07, profit_margin=0.25,
        debt_to_equity=1.5, roe=0.15, free_cash_flow=9.0e10,
        analyst_rating_avg=2.1, number_of_analyst_opinions=42,
        fifty_two_week_high=180.0, fifty_two_week_low=120.0,
    )
    assert r.peg == 1.8
    assert r.fifty_two_week_low == 120.0

def test_company_ratios_new_fields_default_none():
    r = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 10))
    for f in ("peg", "revenue_growth_yoy", "profit_margin", "debt_to_equity",
              "roe", "free_cash_flow", "analyst_rating_avg",
              "number_of_analyst_opinions", "fifty_two_week_high",
              "fifty_two_week_low"):
        assert getattr(r, f) is None
```

- [ ] **Step 2: Run test — expect FAIL on AttributeError / ValidationError**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/models/test_company_ratios.py -v
```

- [ ] **Step 3: Add the 10 fields**

In `src/data/models/company_ratios.py`, add these fields to the `CompanyRatios` model (all `float | None = None` except `number_of_analyst_opinions: int | None = None`):

```python
# Fundamental ratios (audit 1.5; populated by pit_composite or stats provider)
peg: float | None = None                          # PEG ratio
revenue_growth_yoy: float | None = None
profit_margin: float | None = None
debt_to_equity: float | None = None
roe: float | None = None                          # return on equity
free_cash_flow: float | None = None
analyst_rating_avg: float | None = None           # 1.0=Strong Buy ... 5.0=Sell
number_of_analyst_opinions: int | None = None

# 52-week extremes (audit 2.1; populated by stats/yfinance)
fifty_two_week_high: float | None = None
fifty_two_week_low: float | None = None
```

- [ ] **Step 4: Run test — expect PASS**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/models/test_company_ratios.py -v
```

### Task 1.2: Extend `Filing` with `body_excerpt` and `items_8k`

**Files:**
- Modify: `src/data/models/filings.py`
- Test: `tests/unit/data/models/test_filings.py` (new if missing)

- [ ] **Step 1: Write the failing test**

```python
from data.models.filings import Filing
from datetime import datetime, timezone

def test_filing_accepts_body_excerpt_and_items():
    f = Filing(
        ticker="AAPL", form_type="8-K",
        filed_at=datetime(2023, 3, 10, 12, 0, tzinfo=timezone.utc),
        accession_no="0000000000-00-000001",
        body_excerpt="Apple Inc. announced...", items_8k=["2.02", "9.01"],
    )
    assert f.body_excerpt.startswith("Apple")
    assert f.items_8k == ["2.02", "9.01"]

def test_filing_new_fields_default():
    f = Filing(ticker="AAPL", form_type="10-K",
               filed_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
               accession_no="x")
    assert f.body_excerpt is None
    assert f.items_8k == []
```

- [ ] **Step 2: Run — expect FAIL**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/data/models/test_filings.py -v
```

- [ ] **Step 3: Add fields**

Add to `Filing`:

```python
# 8-K body capture (Phase 4 filings/edgar populates these)
body_excerpt: str | None = None              # first ~1,500 chars of main body
items_8k: list[str] = Field(default_factory=list)   # e.g. ["2.02", "9.01"]
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.3: Extend `InsiderTrade` with reporter-flag booleans

**Files:**
- Modify: `src/data/models/trades.py`
- Test: `tests/unit/data/models/test_trades.py`

- [ ] **Step 1: Write the failing test**

```python
from data.models.trades import InsiderTrade
from datetime import date

def test_insider_trade_reporter_flags():
    t = InsiderTrade(
        ticker="AAPL", side="BUY", shares=1000, price_per_share=100.0,
        insider_name="Doe, Jane", insider_title="CFO",
        transaction_code="P", transaction_date=date(2023, 3, 10),
        filed_at=date(2023, 3, 11),
        is_officer=True, is_director=False, is_ten_percent_owner=False,
    )
    assert t.is_officer is True
    assert t.is_director is False

def test_insider_trade_reporter_flags_default_false():
    t = InsiderTrade(
        ticker="AAPL", side="BUY", shares=1, price_per_share=1.0,
        insider_name="X", insider_title="Y", transaction_code="P",
        transaction_date=date(2023, 1, 1), filed_at=date(2023, 1, 2),
    )
    assert t.is_officer is False
    assert t.is_director is False
    assert t.is_ten_percent_owner is False
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add fields to `InsiderTrade`**

```python
# Reporter flags from Form 4 reportingOwner.reportingOwnerRelationship XML
# (audit 2.5). Authoritative replacement for the _role_rank() title regex.
is_officer: bool = False
is_director: bool = False
is_ten_percent_owner: bool = False
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.4: Extend `InsiderDerivativeTrade` with Table II extras

**Files:**
- Modify: `src/data/models/trades.py`
- Test: `tests/unit/data/models/test_trades.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from data.models.trades import InsiderDerivativeTrade
from datetime import date

def test_insider_derivative_table_ii_extras():
    d = InsiderDerivativeTrade(
        ticker="AAPL", insider_name="Doe", insider_title="CFO",
        transaction_code="A", security_title="Stock Option (Right to Buy)",
        underlying_shares=500.0, strike_price=120.0,
        transaction_date=date(2023, 3, 10), filed_at=date(2023, 3, 11),
        expiration_date=date(2033, 3, 10),
        is_indirect_ownership=True, is_late_filed=False,
    )
    assert d.expiration_date.year == 2033
    assert d.is_indirect_ownership is True
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add fields to `InsiderDerivativeTrade`**

```python
# Table II extras (audit 2.6) parsed from Form 4 derivativeTransaction XML.
expiration_date: date | None = None
is_indirect_ownership: bool = False         # DirectOrIndirect == "I"
is_late_filed: bool = False                 # filed past 2-business-day window
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.5: ~~Extend `PoliticianTrade`~~ — **dropped from v1**

Row #14 (politician trades) is out of scope for v1 (see plan header scope note + Phase 0 A4 finding).  The existing `PoliticianTrade` model is unchanged; Quiver continues to populate the existing fields when its API key is present.  If a credible free politician-trade source is found in a follow-up plan, this task returns then.

### Task 1.6: Extend `NotableHolder` with body-parsed fields

**Files:**
- Modify: `src/data/models/trades.py`
- Test: `tests/unit/data/models/test_trades.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from data.models.trades import NotableHolder
from datetime import datetime, timezone

def test_notable_holder_body_fields():
    h = NotableHolder(
        ticker="AAPL", holder="Activist LP", form_type="SC 13D",
        filed_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
        accession_no="x", intent="active", is_amendment=False,
        percent_of_class=8.7, shares_held=1_000_000.0,
        purpose_excerpt="Acquired for investment purposes...",
    )
    assert h.percent_of_class == 8.7
    assert h.shares_held == 1_000_000.0
    assert "investment" in h.purpose_excerpt
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add fields to `NotableHolder`**

```python
# Body-parsed cover-page fields (audit 2.9) — Phase 4 notable_holders/edgar
# extends to populate these.
percent_of_class: float | None = None
shares_held: float | None = None
purpose_excerpt: str | None = None       # 13D Item 4 prose; ≤2,000 chars
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.7: ~~Extend `SocialSentimentSnapshot.platform` to include `"stocktwits"`~~ — **dropped from v1**

Row #13 is deferred (see plan header scope note + Phase 0 §A3).  No `"stocktwits"` literal is added to `SocialSentimentSnapshot.platform`; the model stays at its existing set of platform values.  Re-introduce the literal when the live-implementation plan revives the StockTwits provider.

### Task 1.8: Extend `NewsArticle` with `relevance` field

**Files:**
- Modify: `src/data/models/news.py`
- Test: `tests/unit/data/models/test_news.py` (new if missing)

- [ ] **Step 1: Write the failing test**

```python
from data.models.news import NewsArticle
from datetime import datetime, timezone

def test_news_article_relevance_optional():
    a = NewsArticle(
        ticker="AAPL", headline="x", url="https://x",
        source="alpha_vantage",
        published_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
        sentiment=0.4, relevance=0.85,
    )
    assert a.relevance == 0.85

def test_news_article_relevance_default_none():
    a = NewsArticle(
        ticker="AAPL", headline="x", url="https://x", source="finnhub",
        published_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
    )
    assert a.relevance is None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Add field**

```python
# Alpha Vantage per-ticker per-article relevance score [0.0, 1.0].
# Other news providers leave this None.
relevance: float | None = None
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.9: Create `src/data/models/earnings.py`

**Files:**
- Create: `src/data/models/earnings.py`
- Test: `tests/unit/data/models/test_earnings.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from data.models.earnings import EarningsReport, EarningsHistory
from datetime import date

def test_earnings_report_minimal():
    r = EarningsReport(
        ticker="AAPL", report_date=date(2023, 2, 2),
        fiscal_period="Q1 2023",
    )
    assert r.eps_actual is None

def test_earnings_history_wraps_reports():
    h = EarningsHistory(ticker="AAPL", reports=[
        EarningsReport(ticker="AAPL", report_date=date(2023, 2, 2),
                       fiscal_period="Q1 2023", eps_actual=1.88,
                       eps_estimate=1.94, surprise_pct=-3.1),
    ])
    assert len(h.reports) == 1
    assert h.reports[0].eps_actual == 1.88
```

- [ ] **Step 2: Run — expect FAIL (module not found)**

- [ ] **Step 3: Create the module**

```python
"""Earnings report model — populated by the Finnhub earnings provider."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class EarningsReport(BaseModel):
    """One quarterly earnings report for a single ticker.

    Mirrors the surface Finnhub's earnings_calendar exposes; surprise_pct is
    computed where the provider gives both actual and estimate.
    """

    ticker: str
    report_date: date
    fiscal_period: str                     # e.g. "Q1 2023"
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    surprise_pct: float | None = None      # (actual - estimate) / abs(estimate)


class EarningsHistory(BaseModel):
    """A bundle of recent earnings reports for one ticker.

    Matches the `<Bundle>` / `<History>` pattern used elsewhere in
    `src/data/models/`.
    """

    ticker: str
    reports: list[EarningsReport] = Field(default_factory=list)
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.10: Create `src/data/models/analyst_consensus.py`

**Files:**
- Create: `src/data/models/analyst_consensus.py`
- Test: `tests/unit/data/models/test_analyst_consensus.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from data.models.analyst_consensus import AnalystRating, AnalystRevision
from datetime import date

def test_analyst_rating_minimal():
    r = AnalystRating(ticker="AAPL", as_of=date(2023, 3, 10))
    assert r.target_mean is None

def test_analyst_revision_action_literal():
    r = AnalystRevision(
        ticker="AAPL", firm="GS", action="upgrade",
        from_grade="Neutral", to_grade="Buy",
        event_date=date(2023, 3, 10),
    )
    assert r.action == "upgrade"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create the module**

```python
"""Analyst consensus and revision models — populated by the yfinance
analyst_consensus provider."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class AnalystRating(BaseModel):
    """Consensus snapshot for one ticker as of one date.

    Recommendation scale follows yfinance: 1.0 = Strong Buy, 5.0 = Sell.
    """

    ticker: str
    as_of: date
    target_high: float | None = None
    target_low: float | None = None
    target_mean: float | None = None
    target_median: float | None = None
    recommendation_mean: float | None = None
    number_of_analysts: int | None = None


class AnalystRevision(BaseModel):
    """One upgrade / downgrade / target change event."""

    ticker: str
    firm: str
    action: Literal[
        "upgrade", "downgrade", "initiate",
        "reiterate", "target_raise", "target_cut", "unknown",
    ]
    from_grade: str | None = None
    to_grade: str | None = None
    event_date: date
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.11: Create `src/data/models/short_interest.py`

**Files:**
- Create: `src/data/models/short_interest.py`
- Test: `tests/unit/data/models/test_short_interest.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from data.models.short_interest import ShortInterestSnapshot
from datetime import date

def test_short_interest_snapshot_minimal():
    s = ShortInterestSnapshot(
        ticker="AAPL", settlement_date=date(2023, 2, 28),
        report_publish_date=date(2023, 3, 9), short_interest=100_000_000,
    )
    assert s.short_interest == 100_000_000
    assert s.days_to_cover is None
    # Default source covers the v1-only synthesis path; future true-snapshot
    # providers would pass source="finra_official_snapshot" or similar.
    assert s.source == "finra_regsho_synthesised"
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create the module**

```python
"""Short-interest snapshot model — populated by the FINRA provider.

v1 PROXY CAVEAT (Phase -1 verification 2026-05-17): the only live FINRA
dataset is `regShoDaily` (per-ticker per-venue daily short SALE volume).
The classical NYSE/Nasdaq biweekly OPEN short-interest snapshot is NOT
available on the OAuth tier and there is no sibling endpoint.  v1
therefore ships a synthesised proxy — `short_interest` is the 30-day
cumulative short sale volume; `days_to_cover` is that volume divided by
the 30-day mean daily total volume.  This is correlated with classical
open short interest but is a stock-vs-flow approximation, not the real
thing.  The `source` field marks the synthesis origin so downstream
extractors can disambiguate if (when) a true snapshot provider lands.

The PIT gate is `report_publish_date`.  For the synthesis path it equals
`settlement_date` because regShoDaily publishes T+1 with no biweekly lag;
for any future true-snapshot provider this would diverge again (~8
business-day lag), so backtest queries should still filter on
`report_publish_date <= as_of`, not settlement_date.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel


class ShortInterestSnapshot(BaseModel):
    ticker: str
    settlement_date: date
    report_publish_date: date              # PIT gate (collapses to settlement_date for the synthesis path)
    short_interest: float                  # shares — for the synthesis path this is 30d cumulative short SALE volume (proxy)
    average_daily_volume: float | None = None
    days_to_cover: float | None = None
    source: Literal["finra_regsho_synthesised",
                    "finra_official_snapshot"] = "finra_regsho_synthesised"
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.12: Extend `StockSignalBundle` with three new payload fields

**Files:**
- Modify: `src/data/models/bundle.py`
- Test: `tests/unit/data/models/test_bundle.py` (new if missing)

- [ ] **Step 1: Write the failing test**

```python
from data.models.bundle import StockSignalBundle
from data.models.earnings import EarningsReport
from data.models.analyst_consensus import AnalystRating, AnalystRevision
from data.models.short_interest import ShortInterestSnapshot
from datetime import date, datetime, timezone

def test_bundle_accepts_new_payload_fields():
    b = StockSignalBundle(
        ticker="AAPL",
        generated_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
        earnings=[EarningsReport(ticker="AAPL", report_date=date(2023, 2, 2),
                                 fiscal_period="Q1 2023")],
        analyst_consensus=AnalystRating(ticker="AAPL", as_of=date(2023, 3, 10)),
        analyst_revisions=[],
        short_interest=ShortInterestSnapshot(
            ticker="AAPL", settlement_date=date(2023, 2, 28),
            report_publish_date=date(2023, 3, 9), short_interest=1_000_000,
        ),
    )
    assert len(b.earnings) == 1
    assert b.analyst_consensus is not None
    assert b.short_interest.short_interest == 1_000_000

def test_bundle_new_fields_default_empty():
    b = StockSignalBundle(
        ticker="AAPL",
        generated_at=datetime(2023, 3, 10, tzinfo=timezone.utc),
    )
    assert b.earnings == []
    assert b.analyst_consensus is None
    assert b.analyst_revisions == []
    assert b.short_interest is None
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend `StockSignalBundle`**

Add imports and fields (defaults preserve back-compat with trace files):

```python
from data.models.earnings import EarningsReport
from data.models.analyst_consensus import AnalystRating, AnalystRevision
from data.models.short_interest import ShortInterestSnapshot

# ... inside StockSignalBundle ...
earnings: list[EarningsReport] = Field(default_factory=list)
analyst_consensus: AnalystRating | None = None
analyst_revisions: list[AnalystRevision] = Field(default_factory=list)
short_interest: ShortInterestSnapshot | None = None
```

- [ ] **Step 4: Run — expect PASS**

### Task 1.13: Full fast suite + commit Phase 1

- [ ] **Step 1: Run fast suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

Expected: all green. Existing tests untouched; new model tests pass.

- [ ] **Step 2: Lint**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/data/models/ tests/unit/data/models/
```

- [ ] **Step 3: Commit**

```bash
git add src/data/models/ tests/unit/data/models/
git commit -m "feat(models): extend ratios/filings/trades/news/sentiment + new earnings/consensus/short-interest models

Phase 1 of providers-and-silent-gaps-v1. Additive only — every new field
defaults to None / empty so existing cached trace data round-trips. Adds:

- CompanyRatios: +10 fields (PEG, revenue growth, profit margin, D/E, ROE,
  FCF, analyst rating + opinion count, 52w high/low)
- Filing: +body_excerpt, +items_8k
- InsiderTrade: +is_officer/is_director/is_ten_percent_owner
- InsiderDerivativeTrade: +expiration_date, +is_indirect_ownership, +is_late_filed
- PoliticianTrade: +asset_type, +link, +comment
- NotableHolder: +percent_of_class, +shares_held, +purpose_excerpt
- NewsArticle: +relevance
- New models: EarningsReport/EarningsHistory, AnalystRating/AnalystRevision,
  ShortInterestSnapshot
- StockSignalBundle: +earnings, +analyst_consensus, +analyst_revisions,
  +short_interest
"
```

---

## Phase 2 — Extractor silent-gap fixes

Eleven fixes across five extractor files plus one sector_map helper. Each fix follows the same TDD shape: a unit test that asserts the new feature key is emitted with a non-zero value when input has signal, then the extractor change. Fixes that need a new model field were unblocked by Phase 1; Fix C (relative strength vs SPY/sector) depends on Phase 5 and is wired then.

### Task 2.1: Create `_sector_map.py` helper

**Files:**
- Create: `src/contract/extractors/_sector_map.py`
- Test: `tests/unit/contract/extractors/test_sector_map.py` (new)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors._sector_map import SECTOR_TO_ETF

def test_sector_map_covers_eleven_spdr_sectors():
    assert SECTOR_TO_ETF["Technology"] == "XLK"
    assert SECTOR_TO_ETF["Financial Services"] == "XLF"
    assert SECTOR_TO_ETF["Energy"] == "XLE"
    assert SECTOR_TO_ETF["Healthcare"] == "XLV"
    assert SECTOR_TO_ETF["Consumer Cyclical"] == "XLY"
    assert SECTOR_TO_ETF["Consumer Defensive"] == "XLP"
    assert SECTOR_TO_ETF["Industrials"] == "XLI"
    assert SECTOR_TO_ETF["Basic Materials"] == "XLB"
    assert SECTOR_TO_ETF["Real Estate"] == "XLRE"
    assert SECTOR_TO_ETF["Utilities"] == "XLU"
    assert SECTOR_TO_ETF["Communication Services"] == "XLC"
    assert len(SECTOR_TO_ETF) == 11
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create the helper**

```python
"""yfinance sector string -> SPDR sector ETF symbol mapping.

Keys match the strings yfinance returns in `CompanyRatios.sector`. Used by
the technical extractor to look up the per-ticker sector reference series
out of `state["reference_prices"]`.
"""
from __future__ import annotations

SECTOR_TO_ETF: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Energy":                 "XLE",
    "Healthcare":             "XLV",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Real Estate":            "XLRE",
    "Utilities":              "XLU",
    "Communication Services": "XLC",
}
```

- [ ] **Step 4: Run — expect PASS**

### Task 2.2: Fix A — Technical extractor reads `ratios` sub-key

**Files:**
- Modify: `src/contract/extractors/technical.py`
- Test: `tests/unit/contract/extractors/test_technical.py` (extend or new)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.technical import extract_technical_features
from data.models.company_ratios import CompanyRatios
from datetime import date

def test_technical_emits_golden_cross_when_50d_above_200d():
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=180.0, fifty_day_average=170.0,
        two_hundred_day_average=150.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["golden_cross"] == 1.0
    assert features["death_cross"] == 0.0

def test_technical_emits_death_cross_when_50d_below_200d():
    ratios = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        last_price=140.0, fifty_day_average=145.0,
        two_hundred_day_average=160.0, beta=1.2,
    )
    raw = {"ticker": "AAPL", "bars": [], "ratios": ratios.model_dump()}
    features = extract_technical_features(raw, state={})
    assert features["death_cross"] == 1.0
    assert features["golden_cross"] == 0.0
```

- [ ] **Step 2: Run — expect FAIL (KeyError or zero value)**

- [ ] **Step 3: Implement Fix A**

In `src/contract/extractors/technical.py`, after the existing bar-based feature derivation, add a `_emit_ratios_features(raw)` helper that:

```python
def _emit_ratios_features(raw: dict) -> dict[str, float]:
    """Read raw['ratios'] (already stowed by the fetch callback) and emit
    moving-average crossover + beta-aware features."""
    ratios = raw.get("ratios") or {}
    if not ratios:
        return {}

    last = ratios.get("last_price")
    ma50 = ratios.get("fifty_day_average")
    ma200 = ratios.get("two_hundred_day_average")
    beta = ratios.get("beta")

    out: dict[str, float] = {}
    if last is not None and ma50 is not None and ma200 is not None:
        out["golden_cross"] = 1.0 if ma50 > ma200 and last > ma50 else 0.0
        out["death_cross"]  = 1.0 if ma50 < ma200 and last < ma50 else 0.0
    if beta is not None:
        # Damping factor applied to confidence in the verdict layer; surfaced
        # as a feature so the strategist can audit it.
        out["beta_confidence_damping"] = 1.0 / (1.0 + abs(beta - 1.0))
    return out
```

Merge `_emit_ratios_features(raw)` into the main `extract_technical_features` return dict.

- [ ] **Step 4: Run — expect PASS**

### Task 2.3: Fix B — 52-week distance features

**Files:**
- Modify: `src/contract/extractors/technical.py`
- Test: `tests/unit/contract/extractors/test_technical.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.technical import extract_technical_features
from datetime import datetime, timezone

def _bar(close: float, days_ago: int) -> dict:
    return {
        "timestamp": datetime(2023, 3, 10, tzinfo=timezone.utc).isoformat(),
        "open": close, "high": close, "low": close,
        "close": close, "volume": 1_000_000,
    }

def test_technical_emits_52w_distance_from_bars():
    bars = [_bar(100.0, i) for i in range(260)]
    bars[100]["close"] = 180.0   # 52-week high
    bars[-1]["close"] = 120.0    # current price
    raw = {"ticker": "AAPL", "bars": bars, "ratios": {}}
    features = extract_technical_features(raw, state={})
    assert abs(features["dist_from_high_52w_pct"] - (120.0 - 180.0) / 180.0) < 1e-6
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix B**

Add to `extract_technical_features`:

```python
# 52-week distance — prefer the ratios fast-path, fall back to bars.
ratios = raw.get("ratios") or {}
high52 = ratios.get("fifty_two_week_high")
low52  = ratios.get("fifty_two_week_low")
if (high52 is None or low52 is None) and bars:
    closes = [b["close"] for b in bars[-252:] if b.get("close") is not None]
    if closes:
        high52 = high52 if high52 is not None else max(closes)
        low52  = low52  if low52  is not None else min(closes)

last = (bars[-1]["close"] if bars else None) or ratios.get("last_price")
if last is not None and high52:
    features["dist_from_high_52w_pct"] = (last - high52) / high52
if last is not None and low52:
    features["dist_from_low_52w_pct"]  = (last - low52)  / low52
```

- [ ] **Step 4: Run — expect PASS**

### Task 2.4: Fix D — Fundamental extractor wires 8 ratio fields

**Files:**
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/contract/extractors/test_fundamental.py` (new or extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.fundamental import extract_fundamental_features
from data.models.company_ratios import CompanyRatios
from datetime import date

def test_fundamental_emits_eight_ratio_features():
    r = CompanyRatios(
        ticker="AAPL", as_of=date(2023, 3, 10),
        peg=1.8, revenue_growth_yoy=0.07, profit_margin=0.25,
        debt_to_equity=1.5, roe=0.15, free_cash_flow=9.0e10,
        analyst_rating_avg=2.1, number_of_analyst_opinions=42,
    )
    raw = {"ticker": "AAPL", "ratios": r.model_dump()}
    features = extract_fundamental_features(raw, state={})
    assert features["peg"] == 1.8
    assert features["revenue_growth_yoy"] == 0.07
    assert features["profit_margin"] == 0.25
    assert features["debt_to_equity"] == 1.5
    assert features["roe"] == 0.15
    assert features["free_cash_flow"] == 9.0e10
    assert features["analyst_rating_avg"] == 2.1
    assert features["number_of_analyst_opinions"] == 42
```

- [ ] **Step 2: Run — expect FAIL (missing keys or zero values)**

- [ ] **Step 3: Wire the 8 fields in `_extract_stats_features` (or equivalent)**

Verify `_KEYS` already contains the eight names; if it doesn't, add them. Confirm the iteration loop pulls from `raw["ratios"]` rather than a sibling key. Add fields to `_KEYS`:

```python
_KEYS = (
    # ... existing keys ...
    "peg", "revenue_growth_yoy", "profit_margin", "debt_to_equity",
    "roe", "free_cash_flow", "analyst_rating_avg",
    "number_of_analyst_opinions",
)
```

- [ ] **Step 4: Run — expect PASS**

### Task 2.5: Fix E — Split insider net dollars into per-code aggregates

**Files:**
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/contract/extractors/test_fundamental.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.fundamental import extract_fundamental_features
from data.models.trades import InsiderTrade
from datetime import date

def test_fundamental_splits_insider_dollars_by_transaction_code():
    trades = [
        InsiderTrade(ticker="AAPL", side="BUY", shares=1000,
                     price_per_share=100, insider_name="A", insider_title="CFO",
                     transaction_code="P",
                     transaction_date=date(2023, 3, 5),
                     filed_at=date(2023, 3, 6)).model_dump(),
        InsiderTrade(ticker="AAPL", side="SELL", shares=500,
                     price_per_share=100, insider_name="B", insider_title="CEO",
                     transaction_code="S",
                     transaction_date=date(2023, 3, 6),
                     filed_at=date(2023, 3, 7)).model_dump(),
        InsiderTrade(ticker="AAPL", side="SELL", shares=200,
                     price_per_share=100, insider_name="C", insider_title="GC",
                     transaction_code="F",
                     transaction_date=date(2023, 3, 7),
                     filed_at=date(2023, 3, 8)).model_dump(),
        InsiderTrade(ticker="AAPL", side="BUY", shares=10,
                     price_per_share=100, insider_name="D", insider_title="VP",
                     transaction_code="G",
                     transaction_date=date(2023, 3, 8),
                     filed_at=date(2023, 3, 9)).model_dump(),
    ]
    raw = {"ticker": "AAPL", "insider_trades": trades, "ratios": {}}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["insider_open_market_buy_dollars_30d"] == 100_000
    assert f["insider_open_market_sell_dollars_30d"] == 50_000
    assert f["insider_tax_withholding_dollars_30d"] == 20_000
    assert f["insider_gift_count_30d"] == 1
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix E**

In `fundamental.py`, replace the current `insider_net_dollars_30d` aggregator with a per-code breakdown:

```python
def _insider_per_code_aggregates(trades: list[dict]) -> dict[str, float]:
    """Split insider trades by transaction_code so the strategist can
    distinguish open-market activity (P/S) from administrative codes (F/G).
    """
    out = {
        "insider_open_market_buy_dollars_30d":  0.0,
        "insider_open_market_sell_dollars_30d": 0.0,
        "insider_tax_withholding_dollars_30d":  0.0,
        "insider_gift_count_30d":               0.0,
    }
    for t in trades:
        code   = t.get("transaction_code") or ""
        shares = float(t.get("shares") or 0.0)
        price  = float(t.get("price_per_share") or 0.0)
        dollars = shares * price
        if code == "P":
            out["insider_open_market_buy_dollars_30d"]  += dollars
        elif code == "S":
            out["insider_open_market_sell_dollars_30d"] += dollars
        elif code == "F":
            out["insider_tax_withholding_dollars_30d"]  += dollars
        elif code == "G":
            out["insider_gift_count_30d"]               += 1
    return out
```

Replace the old aggregate call site; keep the original `insider_net_dollars_30d` key as `buy - sell` of P/S for back-compat.

- [ ] **Step 4: Run — expect PASS**

### Task 2.6: Fix F — Replace `_role_rank()` regex with reporter flags

**Files:**
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/contract/extractors/test_fundamental.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.fundamental import extract_fundamental_features
from data.models.trades import InsiderTrade
from datetime import date

def test_fundamental_weights_senior_officer_trades_via_flags():
    """Senior officer (is_officer=True) buys should weight higher than
    non-officer buys of equal dollar size in the senior-buy feature."""
    senior = InsiderTrade(
        ticker="AAPL", side="BUY", shares=1000, price_per_share=100,
        insider_name="CEO", insider_title="Chief Executive Officer",
        transaction_code="P",
        transaction_date=date(2023, 3, 5), filed_at=date(2023, 3, 6),
        is_officer=True, is_director=True,
    ).model_dump()
    junior = InsiderTrade(
        ticker="AAPL", side="BUY", shares=1000, price_per_share=100,
        insider_name="VP", insider_title="VP of Engineering",
        transaction_code="P",
        transaction_date=date(2023, 3, 5), filed_at=date(2023, 3, 6),
        is_officer=False, is_director=False,
    ).model_dump()
    raw = {"ticker": "AAPL", "insider_trades": [senior, junior], "ratios": {}}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["senior_officer_buy_dollars_30d"] == 100_000
    assert "_role_rank" not in dir(__import__(
        "contract.extractors.fundamental", fromlist=["x"]))
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix F**

Delete `_role_rank()` from `fundamental.py`. Replace every call site with reads of `t.get("is_officer")` / `t.get("is_director")` / `t.get("is_ten_percent_owner")`. Add a `senior_officer_buy_dollars_30d` aggregate (filters on `is_officer=True` AND `transaction_code=="P"`).

- [ ] **Step 4: Run — expect PASS**

### Task 2.7: Fix G — Derivative-table features

**Files:**
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/contract/extractors/test_fundamental.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.fundamental import extract_fundamental_features
from data.models.trades import InsiderDerivativeTrade
from datetime import date

def test_fundamental_emits_derivative_features():
    derivs = [
        InsiderDerivativeTrade(
            ticker="AAPL", insider_name="CEO", insider_title="CEO",
            transaction_code="M", security_title="Stock Option",
            underlying_shares=1000.0, strike_price=120.0,
            transaction_date=date(2023, 3, 5), filed_at=date(2023, 3, 6),
            is_officer=True,
        ).model_dump(),
        InsiderDerivativeTrade(
            ticker="AAPL", insider_name="Dir", insider_title="Director",
            transaction_code="A", security_title="RSU",
            underlying_shares=500.0, strike_price=0.0,
            transaction_date=date(2023, 3, 7), filed_at=date(2023, 3, 8),
            is_officer=True,
        ).model_dump(),
    ]
    ratios = {"last_price": 170.0}
    raw = {"ticker": "AAPL", "insider_trades": [], "ratios": ratios,
           "insider_derivative_trades": derivs}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["insider_option_exercise_value_30d"] == 1000 * (170.0 - 120.0)
    assert f["senior_officer_derivative_grant_shares_30d"] == 500.0
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix G**

Add a `_derivative_aggregates(derivs, last_price)` helper following the same shape as `_insider_per_code_aggregates`. Emit:
- `insider_option_exercise_value_30d`: sum over `transaction_code=="M"` of `underlying_shares * (last_price - strike_price)`.
- `insider_derivative_planned_ratio_30d`: ratio of planned (10b5-1) derivative shares to total derivative shares in window — mirror the existing `insider_planned_sale_ratio_30d` shape.
- `senior_officer_derivative_grant_shares_30d`: sum of `underlying_shares` filtered to `is_officer=True` and `transaction_code=="A"`.

Wire helper into the main extractor return dict.

- [ ] **Step 4: Run — expect PASS**

### Task 2.8: Fix H — 8-K item counters

**Files:**
- Modify: `src/contract/extractors/fundamental.py`
- Test: `tests/unit/contract/extractors/test_fundamental.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.fundamental import extract_fundamental_features
from data.models.filings import Filing
from datetime import datetime, timezone, date

def test_fundamental_counts_8k_items_in_30d_window():
    filings = [
        Filing(ticker="AAPL", form_type="8-K",
               filed_at=datetime(2023, 3, 5, tzinfo=timezone.utc),
               accession_no="x1", items_8k=["5.02"]).model_dump(),
        Filing(ticker="AAPL", form_type="8-K",
               filed_at=datetime(2023, 3, 6, tzinfo=timezone.utc),
               accession_no="x2", items_8k=["2.02", "9.01"]).model_dump(),
        Filing(ticker="AAPL", form_type="8-K",
               filed_at=datetime(2023, 3, 7, tzinfo=timezone.utc),
               accession_no="x3", items_8k=["1.01"]).model_dump(),
    ]
    raw = {"ticker": "AAPL", "filings": filings, "ratios": {},
           "insider_trades": []}
    f = extract_fundamental_features(
        raw, state={"as_of": date(2023, 3, 10).isoformat()})
    assert f["n_item_502_30d"] == 1
    assert f["n_item_202_30d"] == 1
    assert f["n_item_101_30d"] == 1
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix H**

```python
def _item_counters_30d(filings: list[dict], as_of: date) -> dict[str, float]:
    """Count 8-K item appearances in the trailing 30 days. Maps the three
    items most relevant to fundamental signals: 5.02 executive departure,
    2.02 earnings release, 1.01 material agreement.
    """
    cutoff = as_of - timedelta(days=30)
    counters = {"n_item_502_30d": 0, "n_item_202_30d": 0, "n_item_101_30d": 0}
    for f in filings:
        if f.get("form_type") != "8-K":
            continue
        filed_dt = _parse_dt(f.get("filed_at"))
        if filed_dt is None or filed_dt.date() < cutoff:
            continue
        items = f.get("items_8k") or []
        if "5.02" in items: counters["n_item_502_30d"] += 1
        if "2.02" in items: counters["n_item_202_30d"] += 1
        if "1.01" in items: counters["n_item_101_30d"] += 1
    return {k: float(v) for k, v in counters.items()}
```

Wire into the extractor return dict.

- [ ] **Step 4: Run — expect PASS**

### Task 2.9: Fix I — News extractor reads `sentiment` (drop `polarity` lookup)

**Files:**
- Modify: `src/contract/extractors/news.py`
- Test: `tests/unit/contract/extractors/test_news.py` (new or extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.news import extract_news_features
from datetime import datetime, timezone

def test_news_reads_sentiment_field_not_polarity():
    articles = [
        {"ticker": "AAPL", "headline": "Beat", "url": "u", "source": "av",
         "published_at": datetime(2023, 3, 10, 9, tzinfo=timezone.utc).isoformat(),
         "sentiment": 0.5},
        {"ticker": "AAPL", "headline": "Miss", "url": "u", "source": "av",
         "published_at": datetime(2023, 3, 10, 8, tzinfo=timezone.utc).isoformat(),
         "sentiment": -0.3},
    ]
    raw = {"ticker": "AAPL", "articles": articles}
    state = {"as_of": "2023-03-10T12:00:00+00:00"}
    f = extract_news_features(raw, state=state)
    # mean of (0.5, -0.3) == 0.1
    assert abs(f["headline_polarity_mean"] - 0.1) < 1e-9
```

- [ ] **Step 2: Run — expect FAIL (current code reads "polarity")**

- [ ] **Step 3: Implement Fix I**

Find every `item.get("polarity")` in `news.py` and replace with `item.get("sentiment")`. Delete the `polarity` lookup entirely; do not keep a fallback.

- [ ] **Step 4: Run — expect PASS**

### Task 2.10: Fix J — Time-weighted news features

**Files:**
- Modify: `src/contract/extractors/news.py`
- Test: `tests/unit/contract/extractors/test_news.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.news import extract_news_features
from datetime import datetime, timezone

def test_news_emits_time_weighted_counters_and_recency():
    now = datetime(2023, 3, 10, 12, tzinfo=timezone.utc)
    def _art(hours_ago: int, s: float):
        return {"ticker": "AAPL", "headline": "x", "url": "u", "source": "av",
                "published_at": (now.replace(hour=12) - 
                                 __import__("datetime").timedelta(hours=hours_ago)
                                ).isoformat(),
                "sentiment": s}
    raw = {"ticker": "AAPL", "articles": [_art(2, 0.8), _art(50, -0.2),
                                           _art(120, 0.3)]}
    state = {"as_of": now.isoformat()}
    f = extract_news_features(raw, state=state)
    assert f["news_count_24h"] == 1
    assert f["news_count_72h"] == 2
    assert f["hours_since_latest_news"] == 2
    # Recency-weighted: 2h-ago dominates.
    assert f["headline_polarity_recency_weighted"] > 0.3
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix J**

Add at top of `news.py`:

```python
HALF_LIFE_HOURS = 24.0          # exponential decay; constant exposed here
```

Inside the extractor, compute per-article ages from `published_at` and `state["as_of"]`. Emit:
- `news_count_24h`, `news_count_72h`
- `hours_since_latest_news` (float; large default like 9999 if no articles)
- `headline_polarity_recency_weighted` = `sum(sentiment_i * exp(-age_h_i * ln2 / HALF_LIFE_HOURS)) / sum(weights)`

- [ ] **Step 4: Run — expect PASS**

### Task 2.11: Fix K — Social extractor reads typed object; stop flattening

**Scope clarification (Phase -1 verification, 2026-05-17):**  The Social analyst is dead in v1.  Row #13 (StockTwits) is deferred (see plan header), `config/data.json` keeps `social_sentiment: "finnhub"` (which soft-fails to empty), and spec decision 9.3 explicitly permits Social to surface `is_no_data=True` throughout v1.  This task is **forward-readiness work**: when a Row #13 follow-up provider lands and the analyst comes back to life, the extractor must already consume the typed snapshot shape (not the legacy flattened dict).  The extractor's **existing call signature is preserved** — `extract_social_features(raw, ticker, *, as_of=None)` — so call-site churn is zero.  The score-velocity feature stays as a `0.0` placeholder for v1 with an inline comment pointing to the Row #13 follow-up where the per-tick memory-buffer wiring will be added.

**Files:**
- Modify: `src/agents/analysts/social/fetch.py`
- Modify: `src/contract/extractors/social.py`
- Test: `tests/unit/contract/extractors/test_social.py` (new or extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.social import extract_social_features
from data.models.sentiment import SocialSentimentSnapshot
from datetime import datetime, timezone

def test_social_reads_typed_snapshot_score():
    """Extractor must read the new typed-snapshot shape emitted by fetch.py
    after the flatten-removal in Step 3 — signature unchanged."""
    snap = SocialSentimentSnapshot(
        ticker="AAPL", platform="reddit",  # any existing literal value works — StockTwits is deferred (Row #13)
        as_of=datetime(2026, 5, 17, tzinfo=timezone.utc),
        mention_count=100, positive_score=0.65, negative_score=0.15,
        score=0.5,
    )
    raw = {"ticker": "AAPL", "snapshots": [snap.model_dump()],
           "aggregate_score": 0.5}
    f = extract_social_features(raw, "AAPL")
    assert f["social_aggregate_score"] == 0.5
    # v1 placeholder — see Step 4 comment block re: Row #13 follow-up.
    assert f["score_velocity_24h"] == 0.0


def test_social_is_no_data_when_snapshots_empty():
    """Soft-fail branch: dead Social analyst per spec decision 9.3."""
    raw = {"ticker": "AAPL", "snapshots": [], "aggregate_score": None}
    f = extract_social_features(raw, "AAPL")
    assert f["is_no_data"] is True
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Stop flattening in the fetch callback**

In `src/agents/analysts/social/fetch.py` around line 65-72, change the body that flattens `SocialSentiment` to pass the typed snapshots through.  Replace the `raw["snapshots"] = {platform: {...flattened dict...}}` block with:

```python
raw["snapshots"] = [s.model_dump() for s in sentiment.snapshots]
raw["aggregate_score"] = sentiment.aggregate_score
```

- [ ] **Step 4: Update extractor body (signature unchanged)**

In `src/contract/extractors/social.py`, **keep the existing signature** `extract_social_features(raw: dict, ticker: str, *, as_of: datetime | None = None) -> dict[str, float | bool]`.  Rewrite the body to:

1. Read `raw["snapshots"]` as a **list** of typed-snapshot dicts (no more `{"reddit": {...}, "twitter": {...}}` dict-of-dict shape).
2. Read `raw["aggregate_score"]` directly from the raw payload.
3. If `raw["snapshots"]` is empty OR `raw.get("aggregate_score")` is `None`, set `is_no_data=True` and return early with all features at safe defaults (`0`, `0.0`, etc.) — Social soft-fails per decision 9.3.
4. Set `social_aggregate_score = raw["aggregate_score"]`.
5. Sum mention counts across snapshots into `mention_count_total`; bucket by platform into `mention_count_reddit`, `mention_count_twitter` (use `snap["platform"]` to switch).
6. **Score velocity** stays as a `0.0` placeholder with an inline comment:

```python
# v1: score_velocity_24h held at 0.0 — Social analyst is dead in v1
# (Row #13 / StockTwits deferred; see plan header + spec decision 9.3).
# Row #13 follow-up plan will add per-tick memory_buffer wiring to compute:
#   score_velocity_24h = aggregate_score - state["memory_buffer"].get(
#       f"previous_aggregate_score:{ticker}", 0.0)
# Wiring the memory_buffer access here would require extending the
# extractor signature, which we are deliberately deferring until the
# analyst is alive again.  Leaving 0.0 keeps the feature shape stable
# for the no-silent-zero-features test (Social is exempted from that
# assertion per Phase 7 Task 7.1).
score_velocity_24h = 0.0
```

7. `platform_score_disagreement` — keep whatever the current extractor computes (unchanged in v1).

- [ ] **Step 5: Run — expect PASS**

### Task 2.12: Smart-money fix — notable_holders aggregates only

**Re-scoped (was: politician midpoint + notable_holders).**  The politician half is deferred with Row #14 (see plan header).  Only the notable_holders aggregates ship in v1.  The existing `politician_buy_dollars_30d` feature stays untouched; the Quiver provider continues to feed it on the rare days a key is present.

**Files:**
- Modify: `src/contract/extractors/smart_money.py`
- Test: `tests/unit/contract/extractors/test_smart_money.py` (new or extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.smart_money import extract_smart_money_features
from data.models.trades import NotableHolder
from datetime import date, datetime, timezone

def test_smart_money_emits_holder_aggregates():
    holders = [
        NotableHolder(ticker="AAPL", holder="H1", form_type="SC 13D",
                      filed_at=datetime(2023, 3, 5, tzinfo=timezone.utc),
                      accession_no="a", intent="active",
                      is_amendment=False, percent_of_class=8.0,
                      shares_held=500_000.0).model_dump(),
        NotableHolder(ticker="AAPL", holder="H2", form_type="SC 13G",
                      filed_at=datetime(2023, 3, 6, tzinfo=timezone.utc),
                      accession_no="b", intent="passive",
                      is_amendment=True, percent_of_class=5.2,
                      shares_held=320_000.0).model_dump(),
    ]
    raw = {"ticker": "AAPL", "politician_trades": [],
           "notable_holders": holders}
    f = extract_smart_money_features(
        raw, state={"as_of": date(2023, 3, 12).isoformat()})
    assert f["n_active_13d_30d"] == 1
    assert f["n_passive_13g_30d"] == 1
    assert f["n_amendments_30d"] == 1
    assert f["notable_holder_present"] == 1.0
    assert f["max_percent_of_class_30d"] == 8.0
    assert f["total_shares_held_30d"] == 820_000.0
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the holder aggregates**

In `smart_money.py`, read `raw["notable_holders"]` (already a typed list of `NotableHolder.model_dump()` records) and emit six features within the 30-day window anchored on `as_of`:
- `n_active_13d_30d` — count of SC 13D filings with `intent="active"`
- `n_passive_13g_30d` — count of SC 13G filings (intent `"passive"`)
- `n_amendments_30d` — count of `is_amendment=True`
- `notable_holder_present` — `1.0` if any holder rows in window else `0.0`
- `max_percent_of_class_30d` — max non-null `percent_of_class`
- `total_shares_held_30d` — sum of non-null `shares_held`

Leave the existing `politician_buy_dollars_30d` logic untouched — Quiver remains the sole feeder and is soft-failed.

- [ ] **Step 4: Run — expect PASS**

### Task 2.13: Full fast suite + commit Phase 2

- [ ] **Step 1: Run fast suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

- [ ] **Step 2: Lint**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/contract/extractors/ src/agents/analysts/social/ tests/unit/contract/extractors/
```

- [ ] **Step 3: Commit**

```bash
git add src/contract/extractors/ src/agents/analysts/social/fetch.py tests/unit/contract/extractors/
git commit -m "feat(extractors): close 11 silent-gap feature sites; drop polarity lookup; replace _role_rank

Phase 2 of providers-and-silent-gaps-v1. Reads the typed objects the
fetch callbacks already produce instead of re-flattening through dicts.

- technical: open raw['ratios'] for golden/death cross + beta damping;
  emit dist_from_high/low_52w_pct from bars or ratios fast-path
- fundamental: wire 8 ratio fields; split insider net dollars by
  transaction_code (P/S/F/G); delete _role_rank in favour of
  is_officer/is_director/is_ten_percent_owner flags; emit
  insider_option_exercise_value_30d + 2 more derivative features;
  count 8-K items (5.02 / 2.02 / 1.01)
- news: read .sentiment (matching model); emit news_count_24h/72h,
  hours_since_latest_news, recency-weighted polarity
- social: stop flattening in fetch callback; read typed score + velocity
- smart_money: politician amount midpoint + bond filter; consume
  notable_holders aggregates (percent_of_class, shares_held)

Fix C (relative_strength_vs_spy/sector) lands in Phase 5 with the
reference-price plumbing.
"
```

---

## Phase 3 — New provider shells

Five new providers (Stock Watcher and StockTwits dropped per scope note).  Each follows the same shape: `register` decorator, async `fetch(...)` function, mocked-HTTP unit test, optional `@pytest.mark.slow` live integration test.  **Gating:** the two Phase 0 items still in scope (Alpha Vantage news A1, FINRA short interest A2) only start once their preflight notes are signed off as green; if a preflight item is red, that provider lands as `is_no_data=True` per spec §12.

Domain registration: Tasks 3.1, 3.5, 3.6, 3.7 add new domains (`earnings`, `analyst_consensus`, `short_interest`, `options`) to `DOMAINS` in `src/data/registry.py`. Task 3.0 captures that change once.

### Task 3.0: Add four new domains to the registry

**Files:**
- Modify: `src/data/registry.py`
- Test: `tests/unit/data/test_registry.py` (extend; create if missing)

- [ ] **Step 1: Write the failing test**

```python
from data.registry import DOMAINS

def test_registry_knows_phase3_domains():
    for d in ("earnings", "analyst_consensus", "short_interest", "options"):
        assert d in DOMAINS
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend `DOMAINS`**

```python
DOMAINS: frozenset[str] = frozenset({
    "price_history",
    "company_ratios",
    "news",
    "social_sentiment",
    "insider_trades",
    "politician_trades",
    "notable_holders",
    "filings",
    "earnings",            # Phase 3 — Finnhub
    "analyst_consensus",   # Phase 3 — yfinance
    "short_interest",      # Phase 3 — FINRA
    "options",             # Phase 3 — yfinance live-only shell
})
```

- [ ] **Step 4: Run — expect PASS**

### Task 3.1: Finnhub earnings provider (Row #6)

**Files:**
- Create: `src/data/providers/earnings/__init__.py` (empty package marker)
- Create: `src/data/providers/earnings/finnhub.py`
- Create: `tests/unit/data/providers/test_earnings_finnhub_as_of.py`

- [ ] **Step 1: Write the failing test (mocked HTTP)**

```python
import pytest
from unittest.mock import MagicMock
from datetime import date


class _AsyncCM:
    """Tiny async-context-manager that yields a stub httpx response.

    Reused across provider tests; if you find yourself copying this into
    a third file, hoist it into tests/unit/data/providers/conftest.py.
    """

    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *a, **k):
        return self._resp

    async def post(self, *a, **k):
        return self._resp


@pytest.mark.asyncio
async def test_earnings_finnhub_returns_history(monkeypatch):
    from data.providers.earnings import finnhub as mod
    fake_payload = {"earningsCalendar": [
        {"symbol": "AAPL", "date": "2023-02-02", "epsActual": 1.88,
         "epsEstimate": 1.94, "revenueActual": 1.17e11,
         "revenueEstimate": 1.21e11, "quarter": 1, "year": 2023},
    ]}
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_payload
    fake_resp.raise_for_status = lambda: None
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: _AsyncCM(fake_resp))

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10),
                          lookback_quarters=4)
    assert out.reports[0].ticker == "AAPL"
    assert out.reports[0].fiscal_period == "Q1 2023"
    assert out.reports[0].eps_actual == 1.88


@pytest.mark.asyncio
async def test_earnings_finnhub_filters_future_reports(monkeypatch):
    """A report dated after `as_of` must not appear in the result."""
    from data.providers.earnings import finnhub as mod
    fake_payload = {"earningsCalendar": [
        {"symbol": "AAPL", "date": "2023-02-02", "epsActual": 1.88,
         "epsEstimate": 1.94, "quarter": 1, "year": 2023},
        {"symbol": "AAPL", "date": "2023-05-04", "epsActual": 1.52,
         "epsEstimate": 1.43, "quarter": 2, "year": 2023},
    ]}
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_payload
    fake_resp.raise_for_status = lambda: None
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: _AsyncCM(fake_resp))

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10),
                          lookback_quarters=4)
    assert len(out.reports) == 1
    assert out.reports[0].report_date == date(2023, 2, 2)


@pytest.mark.asyncio
async def test_earnings_finnhub_filters_unannounced_rows(monkeypatch):
    """Dual PIT filter (Phase -1 verification 2026-05-17): the Finnhub API
    returns FUTURE-dated rows with epsActual=null even when as_of is in the
    past.  The provider must drop these too — otherwise we'd "know" about
    earnings the moment they were scheduled, not when they were announced.

    Real-world example: probing `from=today, to=today+90d` on 2026-05-17
    returned AAPL Q3 2026 dated 2026-07-29 with epsActual=null.
    """
    from data.providers.earnings import finnhub as mod
    fake_payload = {"earningsCalendar": [
        # Already announced — keep.
        {"symbol": "AAPL", "date": "2023-02-02", "epsActual": 1.88,
         "epsEstimate": 1.94, "quarter": 1, "year": 2023},
        # Scheduled but not yet announced — drop (epsActual=null).
        {"symbol": "AAPL", "date": "2023-02-15", "epsActual": None,
         "epsEstimate": 1.70, "quarter": 1, "year": 2023},
    ]}
    fake_resp = MagicMock()
    fake_resp.json.return_value = fake_payload
    fake_resp.raise_for_status = lambda: None
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: _AsyncCM(fake_resp))

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10),
                          lookback_quarters=4)
    assert len(out.reports) == 1
    assert out.reports[0].eps_actual == 1.88
```

- [ ] **Step 2: Run — expect FAIL (module not found)**

- [ ] **Step 3: Implement the provider**

```python
"""Finnhub earnings_calendar provider — populates EarningsHistory.

Free tier: 60 req/min. Endpoint: GET /calendar/earnings?symbol=&from=&to=&token=
Docs verified via context7 (`mcp__plugin_context7_context7__query-docs`)
before implementation.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx

from data.config import get_config
from data.models.earnings import EarningsHistory, EarningsReport
from data.registry import register


_BASE = "https://finnhub.io/api/v1"


@register(
    domain="earnings",
    name="finnhub",
    upstream="finnhub",
    rate_per_minute=60,
    burst=10,
)
async def fetch(
    symbol: str, *, as_of: date, lookback_quarters: int = 4, **_: Any
) -> EarningsHistory:
    """Return up to `lookback_quarters` earnings reports for `symbol`,
    bounded by `as_of` (no rows with report_date > as_of)."""
    cfg = get_config()
    token = cfg.finnhub_api_key
    if not token:
        return EarningsHistory(ticker=symbol, reports=[])
    # Look back ~1 quarter * lookback (≈90 days each) + 30 days slack.
    start = as_of - timedelta(days=lookback_quarters * 90 + 30)
    params = {"symbol": symbol, "from": start.isoformat(),
              "to": as_of.isoformat(), "token": token}
    timeout = httpx.Timeout(cfg.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(f"{_BASE}/calendar/earnings", params=params)
        resp.raise_for_status()
        payload = resp.json() or {}

    reports: list[EarningsReport] = []
    for row in payload.get("earningsCalendar") or []:
        rdate = date.fromisoformat(row["date"])
        eps_a = row.get("epsActual")

        # Dual PIT filter — verified empirically in Phase -1 (2026-05-17):
        # the Finnhub API returns future-dated rows AND scheduled-but-not-yet-
        # announced rows (epsActual=null) regardless of the `to` window.  Both
        # cases must be dropped or the bot would pre-announce earnings during
        # both backtest and live runtime.
        #   (a) date > as_of            — event hasn't happened yet
        #   (b) epsActual is None/""    — event scheduled but not yet announced
        if rdate > as_of:
            continue
        if eps_a in (None, ""):
            continue

        eps_e = row.get("epsEstimate")
        surprise = None
        if eps_e not in (None, 0):
            surprise = (eps_a - eps_e) / abs(eps_e) * 100.0
        reports.append(EarningsReport(
            ticker=row.get("symbol") or symbol,
            report_date=rdate,
            fiscal_period=f"Q{row.get('quarter')} {row.get('year')}",
            eps_actual=eps_a, eps_estimate=eps_e,
            revenue_actual=row.get("revenueActual"),
            revenue_estimate=row.get("revenueEstimate"),
            surprise_pct=surprise,
        ))
    reports.sort(key=lambda r: r.report_date, reverse=True)
    return EarningsHistory(ticker=symbol, reports=reports[:lookback_quarters])
```

- [ ] **Step 4: Run — expect PASS**

### Task 3.2: Alpha Vantage news provider (Row #12)

**Gating:** Phase 0 Task 0.1 must be green.

**Scope clarification (Phase -1 verification, 2026-05-17):**  Alpha Vantage is the **backtest-fill** news provider — chosen because it has a real 2023 archive (confirmed across Jan/Jun/Dec windows with 21/25/9 articles per week respectively, no rate-limit messaging).  Its **free tier is 25 requests/day**, which means 50 tickers × 1 call/day exceeds the daily budget by 2×.  This is acceptable for backtest cache fill because the fill is a **one-shot, multi-day staggered operation** (≈25 tickers/day → 2 calendar days per ticker-day of history, run overnight repeatedly until the SVB window is full).  Once cached, replay is instant — no further AV calls happen during backtest replay.

The **live news provider is intentionally not selected in v1**.  Per the project's "provider switching must be one config flip" architecture (see project memory), the live runtime news provider will be swapped via a single `config/data.json` edit — `news: "alpha_vantage"` becomes `news: "<paid-provider>"` with zero code change.  Candidate live providers (paid throughput tier, evaluated separately) include: Alpha Vantage paid tier (~$25/mo for 75 req/min), Finnhub paid news endpoint, Polygon.io news, NewsAPI.org.  Selection happens during the live-readiness milestone, not now.

The shell still registers correctly and shares the `news` domain signature with any future swap, so this Task creates no dependency on the live decision.

**Files:**
- Create: `src/data/providers/news/alpha_vantage.py`
- Create: `tests/unit/data/providers/test_news_alpha_vantage_as_of.py`

- [ ] **Step 1: Write the failing test (mocked HTTP)**

```python
import pytest
from unittest.mock import MagicMock
from datetime import date

@pytest.mark.asyncio
async def test_alpha_vantage_populates_sentiment_and_relevance(monkeypatch):
    from data.providers.news import alpha_vantage as mod
    payload = {"feed": [{
        "title": "Apple beats", "url": "https://x", "summary": "...",
        "time_published": "20230310T120000",
        "source": "Reuters", "overall_sentiment_score": 0.45,
        "ticker_sentiment": [
            {"ticker": "AAPL", "relevance_score": "0.87",
             "ticker_sentiment_score": "0.51"},
            {"ticker": "MSFT", "relevance_score": "0.21",
             "ticker_sentiment_score": "0.30"},
        ],
    }]}
    fake = MagicMock(); fake.json.return_value = payload
    fake.raise_for_status = lambda: None
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: _AsyncCM(fake))
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 12),
                          lookback_days=7)
    assert len(out) == 1
    assert out[0].sentiment == 0.45
    assert abs(out[0].relevance - 0.87) < 1e-6
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the provider**

Register at `news` domain, name `alpha_vantage`, upstream `alpha_vantage`, `rate_per_minute=5, burst=2` (Alpha Vantage free tier 25/day = ~1/min; conservative throttle keeps headroom for other AV endpoints).

```python
"""Alpha Vantage NEWS_SENTIMENT provider.

Free tier: 25 requests/day. Historical archive depth verified for the SVB
window in Phase 0 task 0.1 (see preflight-notes.md). Returns
NewsArticle[] with .sentiment and .relevance populated from
overall_sentiment_score and ticker_sentiment[].relevance_score.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from data.config import get_config
from data.models.news import NewsArticle
from data.registry import register


_BASE = "https://www.alphavantage.co/query"


def _parse_ts(s: str) -> datetime:
    # Alpha Vantage uses "YYYYMMDDTHHMMSS" UTC
    return datetime.strptime(s, "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


@register(
    domain="news",
    name="alpha_vantage",
    upstream="alpha_vantage",
    rate_per_minute=5,
    burst=2,
)
async def fetch(
    symbol: str, *, as_of: date, lookback_days: int = 7, **_: Any
) -> list[NewsArticle]:
    cfg = get_config()
    if not cfg.alpha_vantage_api_key:
        return []
    start = as_of - timedelta(days=lookback_days)
    params = {
        "function": "NEWS_SENTIMENT",
        "tickers": symbol,
        "time_from": start.strftime("%Y%m%dT0000"),
        "time_to":   as_of.strftime("%Y%m%dT2359"),
        "limit": 50,
        "apikey": cfg.alpha_vantage_api_key,
    }
    timeout = httpx.Timeout(cfg.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.get(_BASE, params=params)
        resp.raise_for_status()
        payload = resp.json() or {}

    out: list[NewsArticle] = []
    for row in payload.get("feed") or []:
        # Find this symbol's ticker_sentiment block for relevance.
        relevance = None
        for ts in row.get("ticker_sentiment") or []:
            if ts.get("ticker") == symbol:
                try:
                    relevance = float(ts["relevance_score"])
                except (KeyError, TypeError, ValueError):
                    relevance = None
                break
        out.append(NewsArticle(
            ticker=symbol,
            headline=row.get("title") or "",
            url=row.get("url") or "",
            summary=row.get("summary"),
            source=row.get("source") or "alpha_vantage",
            published_at=_parse_ts(row["time_published"]),
            sentiment=row.get("overall_sentiment_score"),
            relevance=relevance,
        ))
    return out
```

- [ ] **Step 4: Run — expect PASS**

### Task 3.3: FINRA short-interest provider (Row #11)

**Gating:** Phase 0 Task 0.1 must be green (the extended `probe_finra_short_interest` asserts the seven-field response shape; if any field disappears, the synthesis below must be re-derived before this task can land).

**Scope clarification (Phase -1 verification, 2026-05-17):**  The original plan considered three FINRA outcomes (a true snapshot dataset, a synthesised one, or no access).  Phase -1 confirmed **outcome (b)** definitively — `regShoDaily` is the only available dataset; there is no `settlementDate` / `currentShortPositionQuantity` / `daysToCoverQuantity` snapshot endpoint.  `shortInterestExch` returns 404 and the `otcMarket` metadata endpoint surfaces no sibling candidate.  Outcome (b) is therefore promoted to the **primary and only path** in v1.

**What this means in practice:**  The "short interest" we report in v1 is **NOT** the classical NYSE/Nasdaq biweekly open short-position snapshot.  It is a **30-day cumulative short SALE volume** synthesised from `regShoDaily`, used as a proxy.  The two metrics are correlated but distinct (a stock-vs-flow distinction — open interest is a stock; sale volume is a flow).  The proxy is good enough for v1's smart-money signal but the field is documented at the model level (Task 1.11) and provider level as a FINRA-derived approximation.  When (if ever) FINRA exposes a true snapshot dataset, swap the provider via a single `config/data.json` flip; the field semantics improve transparently.

**Per-date aggregation gotcha (Phase -1 finding):**  On a single trade date, `regShoDaily` can return **multiple rows per ticker** (different `marketCode` venues — AAPL on 2026-05-07 returned 3 rows with codes `B` plus two others).  The synthesis must sum within-day first, then sum across days, otherwise the metric is under-counted proportional to the number of venues:

```
short_volume_30d = sum_d( sum_v( shortParQuantity[d, v] ) )
                          ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   sum across venues for each day
                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^   sum across days in window
```

**Files:**
- Create: `src/data/providers/short_interest/__init__.py`
- Create: `src/data/providers/short_interest/finra.py`
- Create: `tests/unit/data/providers/test_short_interest_finra_as_of.py`

- [ ] **Step 1: Write the failing test (synthesised snapshot)**

Re-use the `_AsyncCM` helper from Task 3.1 (hoist into `tests/unit/data/providers/conftest.py` if not already there).

```python
import pytest
from unittest.mock import MagicMock
from datetime import date

@pytest.mark.asyncio
async def test_finra_synthesises_30d_snapshot_from_regshodaily(monkeypatch):
    """The provider must synthesise a single ShortInterestSnapshot from a
    rolling 30-day window of regShoDaily rows, summing per-day across
    venues first then across days (Phase -1 finding: AAPL on a single
    date returned 3 rows for 3 marketCode venues — naive sum-across-rows
    under-counts venue-aggregated short volume by 3x)."""
    from data.providers.short_interest import finra as mod

    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "tok-xyz",
                                    "expires_in": 43200}
    token_resp.raise_for_status = lambda: None

    # Two trade dates × 2 venues each.  Per-day venue totals:
    #   2023-03-08: short=10+5=15,   total=100+50=150
    #   2023-03-09: short=20+10=30,  total=200+100=300
    # 30d cumulative short volume = 15 + 30 = 45
    # 30d mean daily total = (150 + 300) / 2 = 225
    # synthesised days_to_cover = 45 / 225 = 0.20
    # settlement_date = max(tradeReportDate) = 2023-03-09
    data_resp = MagicMock()
    data_resp.json.return_value = [
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-08", "marketCode": "B",
         "shortParQuantity": 10, "shortExemptParQuantity": 0,
         "totalParQuantity": 100, "reportingFacilityCode": "NCTRF"},
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-08", "marketCode": "Q",
         "shortParQuantity": 5, "shortExemptParQuantity": 0,
         "totalParQuantity": 50, "reportingFacilityCode": "NCTRF"},
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-09", "marketCode": "B",
         "shortParQuantity": 20, "shortExemptParQuantity": 0,
         "totalParQuantity": 200, "reportingFacilityCode": "NCTRF"},
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-09", "marketCode": "Q",
         "shortParQuantity": 10, "shortExemptParQuantity": 0,
         "totalParQuantity": 100, "reportingFacilityCode": "NCTRF"},
    ]
    data_resp.raise_for_status = lambda: None

    cm_calls = iter([_AsyncCM(token_resp), _AsyncCM(data_resp)])
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: next(cm_calls))

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15),
                          lookback_days=30)
    assert len(out) == 1
    snap = out[0]
    assert snap.ticker == "AAPL"
    assert snap.settlement_date == date(2023, 3, 9)        # max(tradeReportDate)
    assert snap.short_interest == 45.0                     # sum-across-days
    assert snap.average_daily_volume == 225.0              # mean-across-days
    assert abs(snap.days_to_cover - 0.20) < 1e-6
    assert snap.source == "finra_regsho_synthesised"      # proxy marker
    assert snap.report_publish_date == date(2023, 3, 9)    # same as settlement_date for regShoDaily


@pytest.mark.asyncio
async def test_finra_filters_rows_after_as_of(monkeypatch):
    """tradeReportDate > as_of must be dropped before aggregation."""
    from data.providers.short_interest import finra as mod
    token_resp = MagicMock()
    token_resp.json.return_value = {"access_token": "tok",
                                    "expires_in": 43200}
    token_resp.raise_for_status = lambda: None
    data_resp = MagicMock()
    data_resp.json.return_value = [
        # Visible:
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-08", "marketCode": "B",
         "shortParQuantity": 10, "shortExemptParQuantity": 0,
         "totalParQuantity": 100, "reportingFacilityCode": "NCTRF"},
        # Invisible — after as_of:
        {"securitiesInformationProcessorSymbolIdentifier": "AAPL",
         "tradeReportDate": "2023-03-20", "marketCode": "B",
         "shortParQuantity": 999, "shortExemptParQuantity": 0,
         "totalParQuantity": 9999, "reportingFacilityCode": "NCTRF"},
    ]
    data_resp.raise_for_status = lambda: None
    cm_calls = iter([_AsyncCM(token_resp), _AsyncCM(data_resp)])
    monkeypatch.setattr(mod.httpx, "AsyncClient",
                        lambda *a, **k: next(cm_calls))

    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15),
                          lookback_days=30)
    assert len(out) == 1
    assert out[0].short_interest == 10.0                  # 999 row dropped


@pytest.mark.asyncio
async def test_finra_returns_empty_when_no_credentials(monkeypatch):
    """Soft-fail when FINRA OAuth credentials are unset."""
    from data.providers.short_interest import finra as mod
    monkeypatch.setattr(mod, "_get_token", lambda *_: None)
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15))
    assert out == []
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the provider (synthesis path)**

```python
"""FINRA short-interest provider — synthesised from regShoDaily.

v1 ships outcome (b) per Phase -1 verification (2026-05-17): the only
live FINRA dataset is regShoDaily (daily short SALE volume per
ticker per venue).  There is no true short-interest snapshot endpoint
on the free / OAuth tier (shortInterestExch returns 404; otcMarket
metadata exposes no sibling candidate).

The provider synthesises a single ShortInterestSnapshot from the last
`lookback_days` (default 30) of regShoDaily rows:

  short_interest          = sum-across-days( sum-across-venues( shortParQuantity ))
  average_daily_volume    = mean-across-days( sum-across-venues( totalParQuantity ))
  days_to_cover           = short_interest / average_daily_volume
  settlement_date         = max(tradeReportDate)
  report_publish_date     = same as settlement_date (regShoDaily is published
                            T+1 with no biweekly lag, so the PIT gate collapses)
  source                  = "finra_regsho_synthesised"  (proxy marker)

The per-date row aggregation matters: on a single trade date AAPL can
return 3 rows (different marketCode venues).  Naive sum-across-rows
under-counts by the venue count.

PIT gate: drops any row with tradeReportDate > as_of BEFORE aggregation.

OAuth2 client-credentials flow; token cached module-level for ~12h.
Endpoint requires `Accept: application/json` (smoke A2) and returns a
top-level JSON array (no `{"data": [...]}` wrapper).
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

from data.config import get_config
from data.models.short_interest import ShortInterestSnapshot
from data.registry import register


_TOKEN_URL = ("https://ews.fip.finra.org/fip/rest/ews/oauth2/access_token"
              "?grant_type=client_credentials")
_DATA_URL  = "https://api.finra.org/data/group/otcMarket/name/regShoDaily"

# JSON must be requested explicitly; FINRA's default response is CSV.
_JSON_HEADERS = {"Accept": "application/json"}

_token_cache: dict[str, Any] = {"token": None, "expires_at": 0.0}


async def _get_token(cfg) -> str | None:
    """Return a cached bearer token or fetch a fresh one via OAuth2."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now + 60:
        return _token_cache["token"]
    if not (cfg.finra_client_id and cfg.finra_client_secret):
        return None
    auth    = (cfg.finra_client_id, cfg.finra_client_secret)
    timeout = httpx.Timeout(cfg.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
        resp = await client.post(_TOKEN_URL)
        resp.raise_for_status()
        payload = resp.json()
    _token_cache["token"]      = payload["access_token"]
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 43200))
    return _token_cache["token"]


def _synthesise(rows: list[dict], symbol: str,
                as_of: date) -> ShortInterestSnapshot | None:
    """Aggregate raw regShoDaily rows into a single synthesised snapshot.

    Returns None if the window contains no in-PIT rows (caller should
    skip emitting the snapshot in that case).
    """
    # Step 1: drop future rows (PIT gate).
    visible = [r for r in rows
               if date.fromisoformat(r["tradeReportDate"]) <= as_of]
    if not visible:
        return None

    # Step 2: sum-within-day across all venues.
    per_day_short: dict[date, float] = defaultdict(float)
    per_day_total: dict[date, float] = defaultdict(float)
    for r in visible:
        d = date.fromisoformat(r["tradeReportDate"])
        per_day_short[d] += float(r.get("shortParQuantity") or 0)
        per_day_total[d] += float(r.get("totalParQuantity") or 0)

    # Step 3: aggregate across days.
    short_cum   = sum(per_day_short.values())
    total_mean  = sum(per_day_total.values()) / max(len(per_day_total), 1)
    settlement  = max(per_day_short.keys())
    dtc         = (short_cum / total_mean) if total_mean > 0 else None

    return ShortInterestSnapshot(
        ticker=symbol,
        settlement_date=settlement,
        report_publish_date=settlement,             # regShoDaily has no lag
        short_interest=short_cum,
        average_daily_volume=total_mean,
        days_to_cover=dtc,
        source="finra_regsho_synthesised",
    )


@register(
    domain="short_interest",
    name="finra",
    upstream="finra",
    rate_per_minute=30,
    burst=10,
)
async def fetch(
    symbol: str, *, as_of: date, lookback_days: int = 30, **_: Any
) -> list[ShortInterestSnapshot]:
    """Return a single synthesised short-interest snapshot for ``symbol``
    aggregated over the prior ``lookback_days`` of regShoDaily rows."""
    cfg   = get_config()
    token = await _get_token(cfg)
    if not token:
        return []

    start   = as_of - timedelta(days=lookback_days)
    headers = {**_JSON_HEADERS, "Authorization": f"Bearer {token}"}
    params  = {
        "securitiesInformationProcessorSymbolIdentifier": symbol,
        "tradeReportDate": f"ge:{start.isoformat()},le:{as_of.isoformat()}",
        # 30 days * ~3 venues per ticker = ~90 rows; 500 is comfortable.
        "limit": 500,
    }
    timeout = httpx.Timeout(cfg.http_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        resp = await client.get(_DATA_URL, params=params)
        resp.raise_for_status()
        rows = resp.json() or []

    snap = _synthesise(rows, symbol, as_of)
    return [snap] if snap else []
```

- [ ] **Step 4: Run — expect PASS**

### Task 3.4: ~~Stock Watcher politician-trade provider~~ — **dropped from v1**

Row #14 is deferred (see plan header scope note + Phase 0 §A4).  The S3 buckets are 403, the websites no longer resolve, and the only live mirror is stale since 2021.  No `stock_watcher.py` is created; no `stock_watcher_clone_root` setting is added.  `politician_trades/quiver.py` remains in place and continues to soft-fail when its API key is unset.  When a credible free politician-trades source surfaces, re-open Row #14 in a follow-up plan.


### Task 3.5: ~~StockTwits social-sentiment provider (Row #13)~~ — **dropped from v1**

Row #13 is deferred (see plan header scope note + Phase 0 §A3).  StockTwits exposes no historical archive on the free tier — the provider would need a 30-day forward-cache warm-up before producing useful baseline signal, and StockBot is pre-deployment so there is no live clock to accumulate that window against.  No `src/data/providers/social_sentiment/stocktwits.py` is created; `config/data.json` keeps `social_sentiment: "finnhub"` (the existing soft-failing provider); the Social analyst lands as `is_no_data=True` throughout v1 per spec decision 9.3, which is exempted from Phase 7's no-silent-zero-features assertion.  The Phase 0 §A3 notes record the Cloudflare User-Agent gotcha for the live-implementation plan to pick up when Row #13 is revived.

### Task 3.6: yfinance analyst-consensus provider (Row #10)

**Files:**
- Create: `src/data/providers/analyst_consensus/__init__.py`
- Create: `src/data/providers/analyst_consensus/yfinance.py`
- Create: `tests/unit/data/providers/test_analyst_consensus_yfinance.py`

- [ ] **Step 1: Write the failing test**

Monkeypatch `yfinance.Ticker` to return canned `analyst_price_targets` / `upgrades_downgrades` / `recommendations_summary`. Assert returns `(AnalystRating, list[AnalystRevision])` with `target_mean`, `recommendation_mean`, and at least one revision mapped to the `action` Literal correctly.

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the provider**

Register at `analyst_consensus`, name `yfinance`, upstream `yfinance`, `rate_per_minute=60, burst=10` (re-uses existing yfinance limiter; if conflict, adjust to match the existing limiter's params). Wrap `yfinance.Ticker(symbol).analyst_price_targets`, `.upgrades_downgrades`, `.recommendations_summary`; map upgrade/downgrade strings to the `AnalystRevision.action` Literal via a small `_ACTION_MAP` dict (unknown → `"unknown"`).

**Snapshot-only caveat:** yfinance does not expose a historical `as_of` for these tables. The provider records `as_of` in the returned `AnalystRating`, but the values reflect "now", not the requested as_of. Document this in the module docstring and emit a warning when `as_of < today - 7d`.

- [ ] **Step 4: Run — expect PASS**

### Task 3.7: yfinance options live-only shell (Row #4)

**Files:**
- Create: `src/data/providers/options/__init__.py`
- Create: `src/data/providers/options/yfinance.py`
- Create: `tests/unit/data/providers/test_options_yfinance_shell.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from datetime import date

@pytest.mark.asyncio
async def test_options_shell_returns_empty_for_backtest_as_of():
    from data.providers.options import yfinance as mod
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10))
    assert out == {} or out is None or getattr(out, "is_no_data", False)
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the shell**

```python
"""yfinance options — live-only shell.

Snapshot-only; not PIT-correct. Row #4 is dropped from the v1 backtest per
decision 7.1 of docs/data-and-providers.md. This module exists so the
registry has a non-empty entry for the `options` domain — it returns an
empty dict for any backtest `as_of` (anything earlier than today).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from data.registry import register


@register(
    domain="options",
    name="yfinance",
    upstream="yfinance",
    rate_per_minute=60,
    burst=10,
)
async def fetch(symbol: str, *, as_of: date, **_: Any) -> dict[str, Any]:
    """Live-only — returns {} for any as_of in the past. Live-mode caller
    can override by wrapping `yfinance.Ticker(symbol).option_chain(expiry)`.
    """
    if as_of < date.today():
        return {}
    # Live-mode placeholder; live wiring lands in a follow-up spec.
    return {}
```

- [ ] **Step 4: Run — expect PASS**

### Task 3.8: Full fast suite + commit Phase 3

- [ ] **Step 1: Run fast suite (skipping integration / slow)**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

- [ ] **Step 2: Lint**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/ src/data/registry.py tests/unit/data/providers/
```

- [ ] **Step 3: Commit**

```bash
git add src/data/registry.py src/data/providers/earnings src/data/providers/news/alpha_vantage.py src/data/providers/short_interest src/data/providers/analyst_consensus src/data/providers/options tests/unit/data/providers/test_earnings_finnhub_as_of.py tests/unit/data/providers/test_news_alpha_vantage_as_of.py tests/unit/data/providers/test_short_interest_finra_as_of.py tests/unit/data/providers/test_analyst_consensus_yfinance.py tests/unit/data/providers/test_options_yfinance_shell.py tests/unit/data/test_registry.py
git commit -m "feat(providers): add 5 new provider shells gated by Phase 0 preflight

Phase 3 of providers-and-silent-gaps-v1. Each provider implements the
existing async fetch() protocol with @register; rate limits via the
shared _LIMITERS infra. Phase 0 Group A preflight gating noted in each
provider's module docstring.

- earnings/finnhub: EarningsHistory from /calendar/earnings
- news/alpha_vantage: NewsArticle[] with sentiment + relevance from
  NEWS_SENTIMENT (verified for SVB window in preflight A1)
- short_interest/finra: ShortInterestSnapshot[] with report_publish_date
  PIT gate; Accept: application/json required (preflight A2)
- analyst_consensus/yfinance: AnalystRating + AnalystRevision; documents
  snapshot-only caveat for historical as_of
- options/yfinance: live-only shell for registry completeness; backtest
  as_of returns {}
- registry: +4 domains (earnings, analyst_consensus, short_interest, options)

Row #14 (politician trades / Stock Watcher) dropped from v1 per scope
note — Stock Watcher upstream is dead.  Quiver provider stays in place
soft-failing.

Row #13 (social sentiment / StockTwits) dropped from v1 — StockTwits
needs a 30d forward-cache warm-up and StockBot is pre-deployment.
social_sentiment config stays on finnhub (soft-failing); Social analyst
is_no_data=True throughout v1 per spec decision 9.3; the no-silent-zero-
features test in Phase 7 exempts Social.  Re-open in the live-
implementation plan.
"
```

---

## Phase 4 — Existing provider extensions

Six existing adapters change, all additively. Each Task starts with a failing assertion that the new field is populated, then extends the provider.

### Task 4.1: `filings/edgar.py` — populate `body_excerpt` and `items_8k`

**Files:**
- Modify: `src/data/providers/filings/edgar.py`
- Modify: `tests/unit/data/providers/test_filings_edgar_as_of.py` (extend)

**Gotcha confirmed by smoke A6:** `edgartools.Filing.items` returns a **comma-delimited string** (e.g. `"2.02,9.01"`), not a Python list.  `list(filing.items)` therefore iterates the string char-by-char and yields `['2','.','0','2',',','9','.','0','1']` — exactly the bug we are trying to fix.  The implementation must `.split(",")` and strip whitespace per item.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from dataclasses import dataclass
from datetime import date


@dataclass
class _FakeFiling:
    """Stand-in for an edgartools Filing object — exposes the same
    attributes the provider reads (form, filing_date, accession_no,
    items, text(), mda).

    NB: `items` is a comma-delimited string matching the real
    edgartools shape (smoke A6).  The provider must split it.
    """

    form: str
    filing_date: date
    accession_no: str
    items: str = ""           # comma-delimited, e.g. "2.02,9.01"
    body: str = ""
    mda: str | None = None

    def text(self) -> str:
        """Return the filing body text."""
        return self.body


@pytest.mark.asyncio
async def test_filings_edgar_populates_8k_body_and_items(monkeypatch):
    from data.providers.filings import edgar as mod
    fake_filing = _FakeFiling(
        form="8-K", filing_date=date(2023, 3, 10),
        accession_no="0000000000-00-000001",
        items="2.02,9.01", body="Apple Inc. reported..." * 200,
    )
    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [fake_filing])
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), per_form=3)
    eight_k = [f for f in out if f.form_type == "8-K"][0]
    # Items must be a list of clean codes, NOT individual chars.
    assert eight_k.items_8k == ["2.02", "9.01"]
    assert eight_k.body_excerpt is not None
    assert len(eight_k.body_excerpt) <= 1500


@pytest.mark.asyncio
async def test_filings_edgar_handles_whitespace_and_empty_items(monkeypatch):
    """Edgartools sometimes inserts a space after the comma; some
    filings also have no items at all (8-K with only an exhibit)."""
    from data.providers.filings import edgar as mod
    spaced = _FakeFiling(form="8-K", filing_date=date(2023, 3, 10),
                         accession_no="x", items="7.01, 8.01", body="b")
    empty  = _FakeFiling(form="8-K", filing_date=date(2023, 3, 11),
                         accession_no="y", items="",         body="b")
    monkeypatch.setattr(mod, "_iter_filings",
                        lambda *a, **k: [spaced, empty])
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), per_form=3)
    by_acc = {f.accession_no: f for f in out if f.form_type == "8-K"}
    assert by_acc["x"].items_8k == ["7.01", "8.01"]
    assert by_acc["y"].items_8k == []
```

(If the provider doesn't already expose a `_iter_filings` seam, add a tiny internal generator that wraps the edgartools call so the test can monkeypatch it cleanly — one new private function, no public API change.)

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend the 8-K branch**

In the 8-K handling block in `filings/edgar.py`, after the existing `mda_excerpt` population (around `filings/edgar.py:166-167`), add:

```python
# Audit 2.4 — capture the 8-K body so the fundamental extractor can
# count item-type events; bounded at 1,500 chars to keep cache footprint
# similar to mda_excerpt.
body_text = filing.text() or ""
filing_kwargs["body_excerpt"] = body_text[:1500] if body_text else None

# Smoke A6: edgartools serves `filing.items` as a comma-delimited
# string (e.g. "2.02,9.01"), NOT a list.  list(filing.items) would
# iterate the string char-by-char.  Split on comma and strip each part.
raw_items = getattr(filing, "items", "") or ""
filing_kwargs["items_8k"] = [
    p.strip() for p in str(raw_items).split(",") if p.strip()
]
```

For non-8-K forms, leave `items_8k=[]` (the model default).

- [ ] **Step 4: Run — expect PASS**

### Task 4.2: `insider_trades/edgar.py` — surface reporter flags and derivative extras

**Files:**
- Modify: `src/data/providers/insider_trades/edgar.py`
- Modify: `tests/unit/data/providers/test_insider_trades_edgar_as_of.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
import pytest
from xml.etree import ElementTree as ET


@pytest.fixture
def form4_xml_with_officer():
    return ET.fromstring("""<?xml version="1.0"?>
    <ownershipDocument>
      <reportingOwner>
        <reportingOwnerRelationship>
          <isOfficer>1</isOfficer>
          <isDirector>0</isDirector>
          <isTenPercentOwner>0</isTenPercentOwner>
        </reportingOwnerRelationship>
      </reportingOwner>
      <nonDerivativeTable>
        <nonDerivativeTransaction>
          <securityTitle><value>Common Stock</value></securityTitle>
          <transactionDate><value>2023-03-05</value></transactionDate>
          <transactionAmounts>
            <transactionShares><value>1000</value></transactionShares>
            <transactionPricePerShare><value>180.00</value></transactionPricePerShare>
            <transactionAcquiredDisposedCode>
              <value>A</value>
            </transactionAcquiredDisposedCode>
          </transactionAmounts>
          <transactionCoding>
            <transactionCode>P</transactionCode>
          </transactionCoding>
        </nonDerivativeTransaction>
      </nonDerivativeTable>
    </ownershipDocument>""")


@pytest.fixture
def form4_xml_with_derivative():
    return ET.fromstring("""<?xml version="1.0"?>
    <ownershipDocument>
      <reportingOwner>
        <reportingOwnerRelationship>
          <isOfficer>1</isOfficer>
        </reportingOwnerRelationship>
      </reportingOwner>
      <derivativeTable>
        <derivativeTransaction>
          <securityTitle><value>Stock Option (Right to Buy)</value></securityTitle>
          <conversionOrExercisePrice><value>120.0</value></conversionOrExercisePrice>
          <transactionDate><value>2023-03-05</value></transactionDate>
          <expirationDate><value>2033-03-05</value></expirationDate>
          <underlyingSecurity>
            <underlyingSecurityShares><value>500</value></underlyingSecurityShares>
          </underlyingSecurity>
          <ownershipNature>
            <directOrIndirectOwnership><value>I</value></directOrIndirectOwnership>
          </ownershipNature>
          <transactionCoding>
            <transactionCode>A</transactionCode>
          </transactionCoding>
        </derivativeTransaction>
      </derivativeTable>
    </ownershipDocument>""")


def test_insider_trades_edgar_surfaces_reporter_flags(form4_xml_with_officer):
    from data.providers.insider_trades.edgar import _build_trade
    trade = _build_trade(form4_xml_with_officer, ticker="AAPL",
                         filed_at=date(2023, 3, 6))
    assert trade.is_officer is True
    assert trade.is_director is False
    assert trade.is_ten_percent_owner is False


def test_insider_derivative_table_ii_extras(form4_xml_with_derivative):
    from data.providers.insider_trades.edgar import _build_derivative
    deriv = _build_derivative(form4_xml_with_derivative, ticker="AAPL",
                              filed_at=date(2023, 3, 6))
    assert deriv.expiration_date == date(2033, 3, 5)
    assert deriv.is_indirect_ownership is True
    assert deriv.is_late_filed is False
```

(If `_build_trade` / `_build_derivative` currently have different signatures, adapt the call — but the assertions and fixture shape are what matter.)

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend `_build_trade()` and `_build_derivative()`**

In `_build_trade()`:

```python
rel = xml.find(".//reportingOwner/reportingOwnerRelationship") or {}
is_officer = (rel.findtext("isOfficer") or "").strip() in ("1", "true")
is_director = (rel.findtext("isDirector") or "").strip() in ("1", "true")
is_ten_percent = (rel.findtext("isTenPercentOwner") or "").strip() in ("1", "true")

return InsiderTrade(
    # ... existing fields ...
    is_officer=is_officer,
    is_director=is_director,
    is_ten_percent_owner=is_ten_percent,
)
```

In `_build_derivative()`:

```python
exp = xml.findtext(".//derivativeTransaction/expirationDate/value")
direct_indirect = xml.findtext(
    ".//derivativeTransaction/ownershipNature/directOrIndirectOwnership/value") or "D"

filed_dt = ...                   # already parsed
transaction_dt = ...             # already parsed
# Late-filed if filed more than 2 business days after transaction.
is_late = _business_days_between(transaction_dt, filed_dt) > 2

return InsiderDerivativeTrade(
    # ... existing fields ...
    expiration_date=date.fromisoformat(exp) if exp else None,
    is_indirect_ownership=(direct_indirect == "I"),
    is_late_filed=is_late,
)
```

Add a private `_business_days_between(a, b)` helper at module scope.

- [ ] **Step 4: Run — expect PASS**

### Task 4.3: `notable_holders/edgar.py` — body-parse `percent_of_class`, `shares_held`, `purpose_excerpt`

**Scope clarification (Phase -1 verification, 2026-05-17):**  The current `notable_holders/edgar.py` `_build()` only reads filing metadata — it never calls `filing.obj()` or fetches the filing body.  Adding `percent_of_class`, `shares_held`, and `purpose_excerpt` requires per-filing body fetches, which **doubles the EDGAR HTTP roundtrip count per filing** (one for the index entry, one for the body).  Within the 600 req/min EDGAR rate limit this is fine for:

- **Backtest cache fill** — a one-shot operation that can run overnight.  50 tickers × ~3 active 13D/G holders per ticker × ~2 filings per holder over the SVB window ≈ 300 body fetches.  Comfortably inside the budget.
- **Live runtime** — only queries current-month filings, so the per-tick budget impact is negligible (typically zero new bodies per tick for the average ticker).

The doubled cost is therefore acknowledged and intentionally accepted; no circuit breaker or per-ticker cap is added in v1.  The existing `_LIMITERS["edgar"]` token bucket handles the rate cap correctly because each `.text()` call is a fresh HTTP request that the registry-dispatched limiter already covers.

**Files:**
- Modify: `src/data/providers/notable_holders/edgar.py`
- Modify: `tests/unit/data/providers/test_notable_holders_edgar_as_of.py` (extend)

- [ ] **Step 1: Write the failing test**

Re-use the `_FakeFiling` dataclass from Task 4.1 (hoist into `tests/unit/data/providers/conftest.py` if not already there).

```python
import pytest
from datetime import date

@pytest.mark.asyncio
async def test_notable_holders_edgar_parses_cover_page_and_purpose(monkeypatch):
    from data.providers.notable_holders import edgar as mod
    fake = _FakeFiling(
        form="SC 13D", filing_date=date(2023, 3, 10), accession_no="x",
        body=(
            "... Percent of Class: 8.5% "
            "Shares Held: 1,200,000 "
            "Item 4. Purpose of Transaction. "
            "The Reporting Person acquired for investment purposes "
            "Item 5. Interest in Securities of the Issuer."
        ),
    )
    monkeypatch.setattr(mod, "_iter_filings", lambda *a, **k: [fake])
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 15), per_form=3)
    holder = out[0]
    assert abs(holder.percent_of_class - 8.5) < 1e-6
    assert holder.shares_held == 1_200_000.0
    assert "investment purposes" in holder.purpose_excerpt
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend the body parser**

Add two regex constants at module scope:

```python
_RE_PERCENT_OF_CLASS = re.compile(
    r"Percent of [Cc]lass\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*%",
)
_RE_SHARES_HELD = re.compile(
    r"Shares Held\s*[:\-]?\s*([0-9][0-9,\.]*)",
)
_RE_ITEM_4 = re.compile(
    r"Item\s+4\.?\s*Purpose of Transaction.*?(?=Item\s+5\.|Signature)",
    re.IGNORECASE | re.DOTALL,
)


def _parse_cover_page(body: str) -> tuple[float | None, float | None]:
    pct_m = _RE_PERCENT_OF_CLASS.search(body)
    shares_m = _RE_SHARES_HELD.search(body)
    pct = float(pct_m.group(1)) if pct_m else None
    shares = float(shares_m.group(1).replace(",", "")) if shares_m else None
    return pct, shares
```

In the per-filing loop, fetch the filing body (same edgartools `.text()` pattern as `filings/edgar.py:166-167`), feed it through `_parse_cover_page`. For SC 13D, also extract Item 4 prose via `_RE_ITEM_4` and bound it at 2,000 chars; for SC 13G, leave `purpose_excerpt=None`. Guard with the existing `_LIMITERS["edgar"]` (no extra throttle code).

- [ ] **Step 4: Run — expect PASS**

### Task 4.4: `company_ratios/pit_composite.py` — populate 6 new XBRL-derivable ratios

**Files:**
- Modify: `src/data/providers/company_ratios/pit_composite.py`
- Modify: `tests/unit/data/providers/test_company_ratios_pit_composite.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_pit_composite_populates_new_ratios(monkeypatch):
    from data.providers.company_ratios import pit_composite as mod
    # Stub edgartools financial summary + yfinance .info to provide
    # full coverage; assert each new field is non-None.
    fake_xbrl = {
        "revenue_growth_yoy": 0.07, "profit_margin": 0.25,
        "debt_to_equity": 1.5, "roe": 0.15, "free_cash_flow": 9.0e10,
        "peg": 1.8,
    }
    monkeypatch.setattr(mod, "_load_xbrl_summary",
                        lambda *a, **k: fake_xbrl)
    ratios = await mod.fetch("AAPL", as_of=date(2023, 3, 10))
    assert ratios.peg == 1.8
    assert ratios.revenue_growth_yoy == 0.07
    assert ratios.profit_margin == 0.25
    assert ratios.debt_to_equity == 1.5
    assert ratios.roe == 0.15
    assert ratios.free_cash_flow == 9.0e10
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend the provider — explicit XBRL concept mapping**

Add a `_load_xbrl_summary(ticker, as_of)` helper that queries `EntityFacts` via `edgartools.Company(ticker).get_facts().query()` and PIT-filters with `.as_of(as_of)`.  The helper returns a dict keyed by the six new `CompanyRatios` field names, with each value either a float or `None` if any required XBRL concept is missing for that ticker.  Wire the dict into the existing `CompanyRatios(...)` constructor invocation in `pit_composite.py`.

**XBRL concept → field arithmetic.**  Each derived metric uses the most recent TTM (trailing twelve months) period available at `as_of`.  Concept names follow the US GAAP taxonomy; `edgartools` exposes them via `.by_concept("us-gaap:ConceptName").as_of(date).value`.  Missing concepts → field stays `None` (do not raise; provider must continue with partial coverage).

| Field | Formula | XBRL concepts required |
|---|---|---|
| `profit_margin` | `net_income / revenue` | `us-gaap:NetIncomeLoss` ÷ `us-gaap:Revenues` (TTM, both) |
| `debt_to_equity` | `total_debt / stockholders_equity` | `(us-gaap:LongTermDebtNoncurrent + us-gaap:LongTermDebtCurrent + us-gaap:ShortTermBorrowings)` ÷ `us-gaap:StockholdersEquity`.  Missing addends default to 0; if `StockholdersEquity` ≤ 0, return `None` (negative equity makes the ratio meaningless) |
| `roe` | `net_income / stockholders_equity` | `us-gaap:NetIncomeLoss` (TTM) ÷ `us-gaap:StockholdersEquity` (most-recent point-in-time).  Same negative-equity guard |
| `revenue_growth_yoy` | `(rev_TTM - rev_TTM_minus_1y) / rev_TTM_minus_1y` | Two `us-gaap:Revenues` queries — one at `as_of`, one at `as_of - 1 year`.  If either is None or zero, return `None` |
| `free_cash_flow` | `operating_cash_flow - capex` | `us-gaap:NetCashProvidedByUsedInOperatingActivities` (TTM) − `us-gaap:PaymentsToAcquirePropertyPlantAndEquipment` (TTM).  Either missing → return `None` |
| `peg` | `trailing_pe / eps_growth_pct` | Needs forward EPS estimate which is **not in XBRL** (analyst estimates are broker-sourced, not filed).  Two-source fallback: (a) read `yfinance.Ticker(symbol).info.get("pegRatio")` if non-None; (b) otherwise return `None`.  Tag PEG values sourced via (a) in the provider's per-field `errors` dict as `peg: yfinance_snapshot_leak` so the manifest can surface the snapshot-leak risk |

**Forward PE and analyst fields.**  `forward_pe`, `analyst_rating_avg`, `number_of_analyst_opinions` are NOT in XBRL — they originate from broker estimate aggregators.  Keep the existing yfinance source path (`Ticker.info["forwardPE"]`, `Ticker.info["recommendationMean"]`, `Ticker.info["numberOfAnalystOpinions"]`) but tag them in the provider's `errors` field (e.g. `forward_pe: yfinance_snapshot_leak`) so the run manifest can flag that the value reflects "today" not `as_of`.  This is the same snapshot-leak risk as PEG-via-yfinance.

**52-week extremes (`fifty_two_week_high`, `fifty_two_week_low`).**  Populated by Task 4.6 (`stats/yfinance.py`), not this provider.  Leave `None` here.

**Soft-fail behaviour.**  If `edgartools.Company(ticker).get_facts()` raises (no XBRL data for ticker — e.g. ADRs, recent IPOs, foreign filers), return an empty dict — the provider then emits `CompanyRatios` with all six new fields as `None`.  Existing 9 fields (long_name, sector, market_cap, trailing_pe, dividend_yield, 50d/200d MA, last_price) remain populated by the unchanged yfinance branch.

- [ ] **Step 4: Run — expect PASS**

### Task 4.5: `politician_trades/quiver.py` — PIT date-filter bug fix

**Files:**
- Modify: `src/data/providers/politician_trades/quiver.py`
- Modify: `tests/unit/data/providers/test_politician_trades_quiver_as_of.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_quiver_filters_on_disclosure_date_not_transaction_date(
    monkeypatch,
):
    from data.providers.politician_trades import quiver as mod
    fake_rows = [
        # transaction_date in window but disclosure_date AFTER as_of:
        # must be filtered out (the market didn't see it yet).
        {"Representative": "X", "Ticker": "AAPL", "Transaction": "Purchase",
         "TransactionDate": "2023-03-02", "DisclosureDate": "2023-03-20",
         "Range": "$15,000 - $50,000"},
        # both dates safely in window: must be included.
        {"Representative": "Y", "Ticker": "AAPL", "Transaction": "Purchase",
         "TransactionDate": "2023-03-01", "DisclosureDate": "2023-03-05",
         "Range": "$1,001 - $15,000"},
    ]
    monkeypatch.setattr(mod, "_load_rows",
                        lambda *a, **k: fake_rows)
    out = await mod.fetch("AAPL", as_of=date(2023, 3, 10), lookback_days=90)
    politicians = {t.politician for t in out}
    assert politicians == {"Y"}
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Change the filter**

Locate the date filter in `quiver.py` (currently `transaction_date <= as_of`); change to `disclosure_date <= as_of`. One-line correctness fix — no further refactor.

- [ ] **Step 4: Run — expect PASS**

### Task 4.6: `stats/yfinance.py` — surface 52-week extremes + analyst counters

**Files:**
- Modify: `src/data/providers/stats/yfinance.py`
- Modify: `tests/unit/data/providers/test_stats_yfinance_as_of.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_stats_yfinance_surfaces_52w_and_analyst_fields(monkeypatch):
    from data.providers.stats import yfinance as mod
    fake_info = {
        "fiftyDayAverage": 170.0, "twoHundredDayAverage": 150.0,
        "fiftyTwoWeekHigh": 180.0, "fiftyTwoWeekLow": 120.0,
        "recommendationMean": 2.1, "numberOfAnalystOpinions": 42,
        "beta": 1.2, "marketCap": 2.7e12,
    }
    monkeypatch.setattr(mod, "_fetch_info_dict", lambda *a, **k: fake_info)
    ratios = await mod.fetch_company_ratios("AAPL", as_of=date(2023, 3, 10))
    assert ratios.fifty_two_week_high == 180.0
    assert ratios.fifty_two_week_low == 120.0
    assert ratios.analyst_rating_avg == 2.1
    assert ratios.number_of_analyst_opinions == 42
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Extend the projection**

Add the four new keys to the existing `_KEEP` projection (or equivalent constant) inside `stats/yfinance.py`:

```python
_KEEP = (
    # existing keys ...
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "recommendationMean", "numberOfAnalystOpinions",
)
```

Map them through to the `CompanyRatios(...)` constructor:

```python
fifty_two_week_high=info.get("fiftyTwoWeekHigh"),
fifty_two_week_low=info.get("fiftyTwoWeekLow"),
analyst_rating_avg=info.get("recommendationMean"),
number_of_analyst_opinions=info.get("numberOfAnalystOpinions"),
```

- [ ] **Step 4: Run — expect PASS**

### Task 4.7: Full fast suite + commit Phase 4

- [ ] **Step 1: Run fast suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

- [ ] **Step 2: Lint**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/data/providers/ tests/unit/data/providers/
```

- [ ] **Step 3: Commit**

```bash
git add src/data/providers/filings/edgar.py src/data/providers/insider_trades/edgar.py src/data/providers/notable_holders/edgar.py src/data/providers/company_ratios/pit_composite.py src/data/providers/politician_trades/quiver.py src/data/providers/stats/yfinance.py tests/unit/data/providers/
git commit -m "feat(providers): extend 6 existing adapters to populate Phase 1 model fields

Phase 4 of providers-and-silent-gaps-v1. All extensions additive.

- filings/edgar: populate body_excerpt + items_8k on 8-K forms
- insider_trades/edgar: surface isOfficer/isDirector/isTenPercentOwner
  reporter flags + Table II expiration_date/direct-or-indirect/late-filed
- notable_holders/edgar: body-parse percent_of_class + shares_held;
  capture Item 4 purpose prose on SC 13D
- company_ratios/pit_composite: populate 6 XBRL-derivable ratios
  (peg, revenue_growth_yoy, profit_margin, debt_to_equity, roe, fcf);
  flag snapshot-leak fields in provider errors
- politician_trades/quiver: bug-fix — filter on disclosure_date not
  transaction_date (STOCK Act PIT semantics)
- stats/yfinance: surface fifty_two_week_high/low + recommendationMean
  + numberOfAnalystOpinions in CompanyRatios projection
"
```

---

## Phase 5 — `state["reference_prices"]` plumbing

Single new responsibility: a pre-tick populator that fetches SPY + 11 sector ETFs in one bulk yfinance call and stows them under `state["reference_prices"]`. Wires Fix C in the technical extractor.

### Task 5.1: Build the pre-tick reference-price populator

**Files:**
- Modify: `src/orchestrator/tick.py`
- Create: `tests/unit/orchestrator/test_tick_reference_prices.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from datetime import date

@pytest.mark.asyncio
async def test_build_initial_state_populates_reference_prices(monkeypatch):
    from orchestrator import tick as mod
    from data.models.price_history import PriceHistory

    fake = {sym: PriceHistory(ticker=sym, bars=[]) for sym in
            ("SPY", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",
             "XLI", "XLB", "XLRE", "XLU", "XLC")}

    async def fake_bulk(symbols, *, as_of, **_):
        return fake

    monkeypatch.setattr(mod, "_fetch_reference_prices", fake_bulk)

    state = await mod._build_initial_state(
        broker=_StubBroker(), tick_id="t1", tickers=["AAPL"]
    )
    assert set(state["reference_prices"].keys()) == set(fake.keys())
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement the populator**

Add to `src/orchestrator/tick.py` (above `_build_initial_state`):

```python
_REFERENCE_SYMBOLS: tuple[str, ...] = (
    "SPY",                                       # market reference
    "XLK", "XLF", "XLE", "XLV", "XLY", "XLP",    # SPDR sector ETFs
    "XLI", "XLB", "XLRE", "XLU", "XLC",
)


async def _fetch_reference_prices(
    symbols: tuple[str, ...], *, as_of: date, period: str = "1y",
    interval: str = "1d",
) -> dict[str, PriceHistory]:
    """Fetch SPY + 11 sector ETFs in one bulk yfinance call.

    A single round-trip is materially faster than 12 sequential
    fetch_price_history calls and avoids burning 12 token-bucket slots
    out of the per-tick yfinance budget.
    """
    return await _bulk_download(symbols, period=period, interval=interval,
                                as_of=as_of)
```

Then in `_build_initial_state`, after the watchlist is resolved:

```python
initial_state["reference_prices"] = await _fetch_reference_prices(
    _REFERENCE_SYMBOLS, as_of=date.today(),
)
```

`_bulk_download` is implemented in Task 5.2.

- [ ] **Step 4: Run — expect PASS**

### Task 5.2: Implement the bulk yfinance helper

**Files:**
- Modify: `src/data/providers/stats/yfinance.py`
- Create: `tests/unit/data/providers/test_stats_yfinance_bulk.py`

- [ ] **Step 1: Write the failing test**

```python
import pytest
from datetime import date

@pytest.mark.asyncio
async def test_bulk_download_returns_one_price_history_per_symbol(monkeypatch):
    import pandas as pd
    from data.providers.stats import yfinance as mod

    def fake_download(tickers, period, interval, **_):
        idx = pd.date_range("2023-01-02", periods=3, freq="D")
        cols = pd.MultiIndex.from_product(
            [["Open", "High", "Low", "Close", "Volume"], tickers])
        return pd.DataFrame(1.0, index=idx, columns=cols)

    monkeypatch.setattr(mod.yf, "download", fake_download)
    out = await mod._bulk_download(
        ("SPY", "XLK"), period="1mo", interval="1d", as_of=date.today())
    assert set(out.keys()) == {"SPY", "XLK"}
    assert all(len(ph.bars) == 3 for ph in out.values())
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `_bulk_download`**

Add at module scope in `stats/yfinance.py`:

```python
async def _bulk_download(
    symbols: tuple[str, ...], *, period: str, interval: str, as_of: date,
) -> dict[str, PriceHistory]:
    """Bulk yfinance download for a set of symbols. Unpacks the MultiIndex
    DataFrame into one PriceHistory per symbol."""
    return await asyncio.to_thread(
        _sync_bulk_download, symbols, period, interval, as_of,
    )


def _sync_bulk_download(
    symbols: tuple[str, ...], period: str, interval: str, as_of: date,
) -> dict[str, PriceHistory]:
    """Sync core of _bulk_download — runs in the thread pool."""
    df = yf.download(list(symbols), period=period, interval=interval,
                     auto_adjust=False, progress=False, threads=True)
    out: dict[str, PriceHistory] = {}
    for sym in symbols:
        bars: list[OHLCBar] = []
        for ts, row in df.iterrows():
            try:
                bars.append(OHLCBar(
                    timestamp=ts.to_pydatetime(),
                    open=float(row[("Open", sym)]),
                    high=float(row[("High", sym)]),
                    low=float(row[("Low", sym)]),
                    close=float(row[("Close", sym)]),
                    volume=int(row[("Volume", sym)]),
                ))
            except (KeyError, ValueError):
                continue
        out[sym] = PriceHistory(ticker=sym, bars=bars)
    return out
```

- [ ] **Step 4: Run — expect PASS**

### Task 5.3: Fix C — `relative_strength_vs_spy/sector` features

**Files:**
- Modify: `src/contract/extractors/technical.py`
- Modify: `tests/unit/contract/extractors/test_technical.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
from contract.extractors.technical import extract_technical_features
from data.models.price_history import PriceHistory
from data.models.company_ratios import CompanyRatios
from datetime import datetime, timezone, date

def _ph(ticker, prices):
    return PriceHistory(ticker=ticker, bars=[
        type("B", (), {"timestamp": datetime(2023, 3, d, tzinfo=timezone.utc),
                       "close": p})()
        for d, p in zip(range(1, len(prices) + 1), prices)
    ])

def test_technical_emits_relative_strength_vs_spy_and_sector():
    bars = [{"timestamp": datetime(2023, 3, d, tzinfo=timezone.utc).isoformat(),
             "close": 100 + d, "open": 100, "high": 110, "low": 90,
             "volume": 1_000_000} for d in range(1, 25)]
    ratios = CompanyRatios(ticker="AAPL", as_of=date(2023, 3, 24),
                           sector="Technology")
    raw = {"ticker": "AAPL", "bars": bars, "ratios": ratios.model_dump()}
    state = {
        "reference_prices": {
            "SPY": _ph("SPY", [100 + d * 0.5 for d in range(1, 25)]),
            "XLK": _ph("XLK", [100 + d * 0.8 for d in range(1, 25)]),
        },
    }
    f = extract_technical_features(raw, state=state)
    # AAPL up ~24%, SPY up ~12%, XLK up ~19% over 24 days.
    assert f["relative_strength_vs_spy_20d"] > 0
    assert f["relative_strength_vs_sector_20d"] > 0
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement Fix C**

Add to `technical.py`:

```python
from contract.extractors._sector_map import SECTOR_TO_ETF


def _pct_change(prices: list[float], window: int) -> float | None:
    if len(prices) <= window:
        return None
    start, end = prices[-window - 1], prices[-1]
    return (end - start) / start if start else None


def _relative_strength(
    own_bars: list[dict], ref_ph, *, window: int,
) -> float | None:
    """Own ticker pct change minus reference pct change over window."""
    if ref_ph is None or not getattr(ref_ph, "bars", None):
        return None
    own_closes = [b["close"] for b in own_bars]
    ref_closes = [b.close for b in ref_ph.bars]
    own_chg = _pct_change(own_closes, window)
    ref_chg = _pct_change(ref_closes, window)
    if own_chg is None or ref_chg is None:
        return None
    return own_chg - ref_chg


# inside extract_technical_features ...
ref_prices = (state or {}).get("reference_prices") or {}
spy_ph = ref_prices.get("SPY")
for w in (5, 20):
    rs_spy = _relative_strength(bars, spy_ph, window=w)
    if rs_spy is not None:
        features[f"relative_strength_vs_spy_{w}d"] = rs_spy

sector = (raw.get("ratios") or {}).get("sector")
sector_etf = SECTOR_TO_ETF.get(sector) if sector else None
sector_ph = ref_prices.get(sector_etf) if sector_etf else None
for w in (5, 20):
    rs_sec = _relative_strength(bars, sector_ph, window=w)
    if rs_sec is not None:
        features[f"relative_strength_vs_sector_{w}d"] = rs_sec
```

- [ ] **Step 4: Run — expect PASS**

### Task 5.4: Full fast suite + commit Phase 5

- [ ] **Step 1: Run fast suite**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

- [ ] **Step 2: Lint**

```bash
PYTHONPATH=src .venv/bin/python -m ruff check src/orchestrator/tick.py src/data/providers/stats/yfinance.py src/contract/extractors/technical.py tests/unit/orchestrator/ tests/unit/contract/extractors/
```

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator/tick.py src/data/providers/stats/yfinance.py src/contract/extractors/technical.py tests/unit/orchestrator/test_tick_reference_prices.py tests/unit/data/providers/test_stats_yfinance_bulk.py tests/unit/contract/extractors/test_technical.py
git commit -m "feat(orchestrator): seed state[reference_prices] from one bulk yfinance call; wire Fix C

Phase 5 of providers-and-silent-gaps-v1. Decision 9.6 of the brief —
SPY + 11 SPDR sector ETFs fetched once per tick and stored under
state['reference_prices'], not duplicated per ticker.

- orchestrator/tick: _fetch_reference_prices + _build_initial_state seed
- stats/yfinance: _bulk_download helper unpacks the MultiIndex DataFrame
  into one PriceHistory per symbol; single round-trip beats 12 sequential
- extractors/technical: Fix C — emit relative_strength_vs_spy_5d/20d and
  relative_strength_vs_sector_5d/20d using the new state key; sector
  lookup via _sector_map.SECTOR_TO_ETF
"
```

---

## Phase 6 — Config + registry wiring

Wire the new providers into `config/data.json`, document them in `config/README.md`, and make sure auto-import in `src/data/providers/__init__.py` picks them up so the boot-time `@register` validation succeeds.

### Task 6.1: Auto-import new provider modules

**Files:**
- Modify: `src/data/providers/__init__.py`
- Test: implicit — `from data.providers import *` must not raise when domains are populated.

- [ ] **Step 1: Write the failing test**

```python
def test_all_phase3_providers_register_on_import():
    import importlib
    importlib.import_module("data.providers")
    from data.registry import _REGISTRY
    expected = {
        ("earnings", "finnhub"),
        ("news", "alpha_vantage"),
        ("short_interest", "finra"),
        ("analyst_consensus", "yfinance"),
        ("options", "yfinance"),
    }
    assert expected.issubset(set(_REGISTRY.keys()))
```

- [ ] **Step 2: Run — expect FAIL (missing registrations)**

- [ ] **Step 3: Add imports**

In `src/data/providers/__init__.py`:

```python
from .analyst_consensus import yfinance as _analyst_consensus_yfinance  # noqa: F401
from .earnings import finnhub as _earnings_finnhub  # noqa: F401
from .news import alpha_vantage as _news_alpha_vantage  # noqa: F401
from .options import yfinance as _options_yfinance  # noqa: F401
from .short_interest import finra as _short_interest_finra  # noqa: F401
```

Do NOT add a `politician_trades.stock_watcher` import — that provider is out of v1 scope (Row #14 deferred).  Do NOT add a `social_sentiment.stocktwits` import — that provider is also out of v1 scope (Row #13 deferred).  The existing `politician_trades.quiver` and `social_sentiment.finnhub` imports in `__init__.py` are left as-is; both soft-fail when their inputs are unavailable.

- [ ] **Step 4: Run — expect PASS**

### Task 6.2: Update `config/data.json` to the v1 stack

**Files:**
- Modify: `config/data.json`
- Test: `tests/unit/data/test_config_data_json.py` (new — validate config matches registry)

- [ ] **Step 1: Write the failing test**

```python
import json
from pathlib import Path

def test_config_data_json_provider_names_resolve_in_registry():
    from data.registry import DOMAINS, _REGISTRY
    import data.providers  # noqa: F401 — force registration
    cfg = json.loads(Path("config/data.json").read_text())
    for domain, name in cfg["providers"].items():
        assert domain in DOMAINS, f"unknown domain in config: {domain}"
        assert (domain, name) in _REGISTRY, f"missing registry entry: ({domain}, {name})"


def test_config_data_json_lists_phase3_domains():
    cfg = json.loads(Path("config/data.json").read_text())
    for d in ("earnings", "analyst_consensus", "short_interest", "options"):
        assert d in cfg["providers"]
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Update `config/data.json`**

Replace the `"providers"` and `"defaults"` blocks.  `politician_trades` keeps its existing value (currently `"fmp"`) — Row #14 is out of v1 scope, so no rewiring there.  `social_sentiment` keeps its existing value (currently `"finnhub"`) — Row #13 is also out of v1 scope; the Social analyst stays on the soft-failing finnhub provider per spec decision 9.3.  Swap `news` from `tiingo` → `alpha_vantage`, and add the four new Phase 3 domain keys.

```json
{
  "providers": {
    "price_history":     "yfinance",
    "company_ratios":    "pit_composite",
    "news":              "alpha_vantage",
    "social_sentiment":  "finnhub",
    "insider_trades":    "edgar",
    "politician_trades": "fmp",
    "notable_holders":   "edgar",
    "filings":           "edgar",
    "earnings":          "finnhub",
    "analyst_consensus": "yfinance",
    "short_interest":    "finra",
    "options":           "yfinance"
  },
  "defaults": {
    "news_lookback_days": 7,
    "insider_lookback_days": 30,
    "politician_lookback_days": 90,
    "notable_holder_lookback_days": 180,
    "notable_holder_limit": 20,
    "history_period": "1y",
    "history_interval": "1d",
    "filings_per_form": 3,
    "include_filing_excerpts": true,
    "earnings_lookback_quarters": 4,
    "short_interest_lookback_days": 90
  },
  "http_timeout_seconds": 15.0
}
```

- [ ] **Step 4: Run — expect PASS**

### Task 6.3: Document new keys in `config/README.md`

**Files:**
- Modify: `config/README.md`

- [ ] **Step 1: Read current `data.json` documentation block**

Find the providers table in `config/README.md`.

- [ ] **Step 2: Append documentation rows**

Add rows for each new domain:

```
| `earnings`           | `finnhub`       | Quarterly EPS / revenue history (last N quarters). |
| `analyst_consensus`  | `yfinance`      | Target prices + revisions. **Snapshot-only**; not PIT-correct for as_of older than ~7 days. |
| `short_interest`     | `finra`         | FINRA exchange-listed short-interest snapshots. PIT-gated on `report_publish_date`. |
| `options`            | `yfinance`      | **Live-only shell.** Backtest as_of returns `{}` — row dropped from v1 per decision 7.1. |
```

Also note the news swap and the social-sentiment hold:

```
- `news` was `tiingo`; now `alpha_vantage` (richer sentiment + per-ticker relevance).
- `social_sentiment` stays on `finnhub` for v1 — Row #13 (StockTwits) is
  deferred to the live-implementation plan because StockTwits needs a
  30-day forward-cache warm-up.  Social analyst soft-fails to
  `is_no_data=True` per spec decision 9.3.
```

- [ ] **Step 3: Lint nothing — config README is markdown**

### Task 6.4: Commit Phase 6

- [ ] **Step 1: Run fast suite (boot validator + config check)**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

- [ ] **Step 2: Commit**

```bash
git add src/data/providers/__init__.py config/data.json config/README.md tests/unit/data/test_config_data_json.py
git commit -m "feat(config): wire Phase 3 providers into data.json + registry auto-import

Phase 6 of providers-and-silent-gaps-v1.

- providers/__init__.py: auto-import all 5 new modules so @register
  fires at boot.  No stocktwits/stock_watcher imports — both deferred.
- config/data.json: news swap tiingo->alpha_vantage; new
  earnings/analyst_consensus/short_interest/options domains; new
  defaults for earnings + short_interest lookbacks.  social_sentiment
  stays on finnhub (Row #13 deferred); politician_trades stays on fmp
  (Row #14 deferred).
- config/README.md: document new domains + snapshot/live-only caveats
- new config-data validator test asserts every (domain, name) pair
  in data.json resolves in the registry
"
```

---

## Phase 7 — SVB-window smoke test + PR open

A single new integration test asserts the spec's end-to-end goal: replay the SVB window and assert no analyst (except Social, per decision 9.3) reports `is_no_data=True` and no feature key silently degrades to `0.0` for >50% of ticks.

### Task 7.1: Add the no-silent-zero-features smoke test

**Files:**
- Create: `tests/integration/backtest/test_no_silent_zero_features.py`

- [ ] **Step 1: Write the test**

```python
"""Smoke test — replay the SVB window and assert the four-extractor
verdict matrix has no silent zero-features. Marked slow + integration
to keep the default test run fast."""
import pytest
from datetime import date

@pytest.mark.slow
@pytest.mark.integration
def test_no_silent_zero_features_on_svb_window(tmp_path):
    from backtest.runner import run_window
    run_dir = run_window(
        window_name="svb-stress-2023-03",
        runs_root=tmp_path / "runs",
    )

    # Read one tick's trace; assert every non-Social analyst emits a
    # verdict (not is_no_data=True) and that at least the headline
    # technical / fundamental / news / smart_money features are present
    # and non-zero where the inputs warranted signal.
    import json
    from pathlib import Path

    trace_files = sorted((Path(run_dir) / "traces").glob("*.json"))
    assert trace_files, "no trace files produced"

    sample = json.loads(trace_files[len(trace_files) // 2].read_text())
    verdicts = sample.get("verdicts") or {}

    for analyst in ("technical", "fundamental", "news", "smart_money"):
        v = verdicts.get(analyst)
        assert v is not None, f"missing {analyst} verdict"
        assert v.get("is_no_data") is not True, (
            f"{analyst} silently degraded to is_no_data on SVB tick"
        )

    # Spot-check a sentinel feature lit up by Phase 1/2/5 work.
    tech_features = verdicts["technical"].get("features") or {}
    assert any(k.startswith("relative_strength_vs_spy_") for k in tech_features), (
        "Fix C did not emit relative_strength_vs_spy features"
    )
```

- [ ] **Step 2: Run — expect to either PASS or surface a specific gap**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/integration/backtest/test_no_silent_zero_features.py -v -m slow
```

(Pre-requisite: SVB-window cache must be rebuilt against the new providers — see Task 7.3. If the cache is stale, this test fails meaningfully; that surfaces an issue to fix before opening the PR rather than after merge.)

- [ ] **Step 3: If failures surface real gaps, fix at root, not by lowering the assertion bar**

If a non-Social analyst still hits `is_no_data=True`, that is a missed
silent-gap — return to the relevant Phase 2 or Phase 4 task and patch.

### Task 7.2: Extend existing end-to-end smoke test with manifest assertion

**Files:**
- Modify: `tests/integration/backtest/test_end_to_end_smoke.py`

- [ ] **Step 1: Locate the manifest-assertion section**

```bash
grep -n "manifest\|verdict\|is_no_data" tests/integration/backtest/test_end_to_end_smoke.py
```

- [ ] **Step 2: Add the assertion**

After the existing manifest checks, add:

```python
# Phase 7 — every analyst except Social emits a non-is_no_data verdict
# on the SVB window. Social explicitly expected to soft-fail per
# decision 9.3 of docs/data-and-providers.md.
non_social = {"technical", "fundamental", "news", "smart_money"}
for analyst in non_social:
    assert manifest_data["verdicts"][analyst]["is_no_data"] is not True, (
        f"{analyst} silently degraded to is_no_data — Phase 2/4 gap"
    )
```

(If `manifest_data` is named differently in this file, adapt — the goal is the assertion, not the variable name.)

### Task 7.3: Rebuild the SVB cache and run the smoke test locally

**Files:** none modified — local cache refresh + verification.

- [ ] **Step 1: Refill the SVB cache with the new providers active**

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch \
    --window svb-stress-2023-03
```

Expect: completes without hard error. Some providers may log per-ticker
warnings — that's fine as long as no domain fails for >50% of tickers.

- [ ] **Step 2: Run the new smoke test**

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
    tests/integration/backtest/test_no_silent_zero_features.py -v -m slow
```

- [ ] **Step 3: Run the end-to-end smoke test**

```bash
PYTHONPATH=src .venv/bin/python -m pytest \
    tests/integration/backtest/test_end_to_end_smoke.py -v -m slow
```

- [ ] **Step 4: Run the fast suite one more time**

```bash
PYTHONPATH=src .venv/bin/python -m pytest tests/ -m "not slow and not integration" -q
```

Expected: all green.

### Task 7.4: Commit Phase 7

- [ ] **Step 1: Commit**

```bash
git add tests/integration/backtest/test_no_silent_zero_features.py tests/integration/backtest/test_end_to_end_smoke.py
git commit -m "test(backtest): SVB-window assertions for no-silent-zero-features

Phase 7 of providers-and-silent-gaps-v1.

- new test_no_silent_zero_features asserts every non-Social analyst
  emits a non-is_no_data verdict and that Fix C's
  relative_strength_vs_spy features are present
- extends test_end_to_end_smoke with the same manifest assertion so
  any future regression that silently drops an analyst signal fails
  the existing smoke test, not just the new one
"
```

### Task 7.5: Open the PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin providers-and-silent-gaps-v1
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --title "Providers & silent gaps v1 (Phases 1-7)" --body "$(cat <<'EOF'
## Summary

- Closes 11 silent-gap extractor sites identified in the free-wins audit.
- Adds 5 new free-tier provider shells (Finnhub earnings, Alpha Vantage
  news, FINRA short interest, yfinance analyst consensus, yfinance
  options live-only shell).  Row #14 (politician trades) and Row #13
  (social sentiment / StockTwits) are dropped from v1 — see plan header
  scope note.
- Extends 6 existing providers additively + bug-fixes the Quiver
  politician-trade date filter (provider stays in place, soft-failing
  to `[]` until a credible free source surfaces).
- Social analyst lands as `is_no_data=True` throughout v1 per spec
  decision 9.3 (the no-silent-zero-features test exempts Social).
- Seeds `state["reference_prices"]` with SPY + 11 SPDR sector ETFs in one
  bulk yfinance call, unblocking `relative_strength_vs_spy/sector`.

## Spec / brief / audit

- Spec: `docs/Phase7-pre-backtest-cleanup/providers-and-silent-gaps-spec.md`
- Brief: `docs/data-and-providers.md`
- Audit: `docs/superpowers/specs/provider-research/free-wins-audit.md`
- Preflight notes: `docs/superpowers/specs/provider-research/preflight-notes.md`

## Test plan

- [x] `pytest -m "not slow and not integration" -q` green on every phase commit
- [x] Local rebuild of SVB-window cache against new providers
- [x] `tests/integration/backtest/test_no_silent_zero_features.py` green
- [x] `tests/integration/backtest/test_end_to_end_smoke.py` green
- [ ] Reviewer to spot-check `config/data.json` swap is intentional
  (`news`: tiingo → alpha_vantage; `social_sentiment`: stays on
  `finnhub` because Row #13 / StockTwits is deferred)

EOF
)"
```

Return the PR URL.

---

## Self-review checklist (run before handing off to execution)

After writing this plan and before execution starts, the plan author verifies:

1. **Spec coverage:** every numbered fix (A-K) from spec §5 has a Phase 2 task. Every new provider (Tasks 3.1-3.7) corresponds to a row in the §6 table. Every Phase 1 model field has a constructor test in Phase 1.
2. **Placeholder scan:** no TBD / TODO / "implement later" / "fill in" / "similar to Task N" markers. Each step has either runnable code or a runnable shell command.
3. **Type consistency:** field names cross-referenced — `is_officer` (not `isOfficer`) in models AND extractors; `report_publish_date` in `ShortInterestSnapshot` matches the FINRA provider's PIT gate; `relevance` (not `relevance_score`) on `NewsArticle`.
4. **Gating order:** Phase 0 commits before Phase 3 starts; registry domains added in Task 3.0 before providers in 3.1-3.7; auto-imports in 6.1 land before `config/data.json` swap in 6.2 so the boot validator doesn't fail mid-phase.

Any issue found → fix inline, don't dispatch.


