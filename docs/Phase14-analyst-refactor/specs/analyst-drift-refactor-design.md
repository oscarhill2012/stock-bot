# Phase 14 — Analyst Drift Refactor: Design

**Status:** Approved in brainstorming session 2026-07-06.
**Branch:** `phase14/analyst-refactor`.
**Plans:** four, written to `docs/Phase14-analyst-refactor/plans/`.

---

## 1. Context and motivation

Phase 13 ran eleven iterations of analyst tuning across the `baseline-2025-09`
and `iran-conflict-2026-02` windows and found **no eval-backed edge** from
incremental improvement of the existing analysts. The post-mortem conclusion:
any price move derivable from *direct, fresh, firm-specific headline
sentiment* is priced in before a twice-daily tick can react to it.

Phase 14 pivots the signal target instead of the analyst quality: stop
chasing the initial reaction, and target the **documented under-reaction
drift** that follows news — effects operating at 1 day to 6 months, which a
twice-daily (or even once-daily) tick cadence can comfortably capture. The
deterministic technical code needs a ~5-day price-change signal, which sits
inside every drift window targeted here.

## 2. Literature basis

| Effect | Horizon | Signal reframe | Key reference |
| --- | --- | --- | --- |
| Post-earnings-announcement drift (PEAD) | 5–90 days | Trade the drift after a genuine surprise, not the jump | Bernard & Thomas; review: Fink (2020), *JBEF* |
| Negative-news drift, small-cap concentration | 1–10 days | Asymmetric weighting of fresh negative news | Lopez-Lira & Tang (2023), arXiv 2304.07619 |
| Economic-links momentum (customer/supplier, sector peers) | ~1 month | News about linked firm A → drift in firm B | Cohen & Frazzini (2008), *JF* |
| Stale-news reversal | ~1 week | Never trade stale news; optionally fade it | Tetlock (2011), "Fit to Reprint" |
| Filing-language changes ("Lazy Prices") | 3–6 months | Diff consecutive 10-K/10-Q; changers underperform | Cohen, Malloy & Nguyen (2020), *JF* |
| Raw macro/geopolitical → single names | weak at ≤1 month | Context only, not standalone alpha | Caldara & Iacoviello GPR literature |

Two caveats recorded up front:

- **Cap-size attenuation.** Every effect above is strongest in small/mid
  caps and weakest in megacaps. The current watchlist is megacap-heavy;
  a null eval result on megacaps does not falsify the channel. Watchlist
  composition is out of scope for *these four plans*, but broadening it
  toward mid-caps is a **committed next step** — the original programme
  goal — sequenced after Plan 2's eval and before the linkage plan
  (backlog B35). It is a precondition for the linkage channel, not an
  afterthought.
- **LLM look-ahead.** Any LLM call in a backtest carries training-knowledge
  contamination risk on pre-cutoff windows. Not new to this design, but the
  exposure map (§6.3) is more knowledge-dependent than most calls and is
  named in Risks (§9).

## 3. Programme structure — four plans and an eval gate

| Plan | Scope | Depends on |
| --- | --- | --- |
| 1 | Filing-delta fundamental signal ("Lazy Prices") | — |
| 2 | News subsystem rebuild (ticker-level drift) | — |
| 3 | Macro stream data plumbing (router + refetch) | — |
| 4 | Linkage analyst (digester, exposure map, matcher, registry) | 2, 3 |

**Eval gate:** Plans 1–2 are built and evaluated first. Plan 4's build
routes through a **two-step gate**: (1) Plan 2's eval must show the drift
reframe has signal, **and then** (2) the watchlist is broadened toward
mid-caps (the original programme goal — see the §2 cap-attenuation caveat
and backlog B35) *before* linkage is built. The economic-links channel is
an attention-constrained effect with no drift-targets in a megacap-only
universe, so building it before the watchlist widens would produce an
uninformative eval. Plans are co-planned: each plan trusts its siblings
land as specified — no defensive shims for state a sibling owns.

**Eval-window dependency:** Plan 1's 3–6-month signal needs long windows;
`long-baseline-2025` requires the pending cache refetch (revenue-concept
fix) first. Plan 2's 5d–1mo signal evaluates on existing windows.

## 4. Hard constraints (session decisions)

- **D1 — No new news providers.** Finnhub `/company-news` is the sole news
  source. All macro/sector input derives from it. (Formatting/normalisation
  risk of multi-provider news judged unacceptable.)
- **D2 — Backtest/live parity is non-negotiable.** The Finnhub general-news
  feed (`/news?category=…`) cannot be queried historically, so it is
  **dropped from the strategy entirely**. The live pipeline must consume
  nothing a backtest cannot reproduce. Consequence accepted: global events
  reach the mapper only through the equity-news lens (roundups + company
  coverage).
- **D3 — Golden-cache refetch is permitted** to populate the macro stream
  for existing windows. Upgrade potential must not be constrained by cache
  preservation.
- **D4 — Token reduction is a requirement**, not an aspiration. The macro
  side adds at most two flash-class LLM calls per tick for the whole
  watchlist; the deterministic staleness pre-filter does the volume kill.

## 5. Plans 1–2 — settled designs (summary)

Full elaboration belongs to their implementation plans; the settled shape:

### Plan 1 — Filing-delta fundamental
- Retrieve the *previous* comparable filing (10-K↔10-K, 10-Q↔10-Q) alongside
  the current one (filings provider exists; cache schema extends).
- Fundamental prompt reframes to **diff-oriented** analysis: what changed in
  MD&A, risk factors, litigation and executive-team language between
  consecutive filings. Sign convention per Lazy Prices: **substantive change
  is bearish by default**; absence of change is quiet-bullish.
- Handle incorporated-by-reference MD&A stubs (e.g. XOM Exhibit 13) — fall
  back to the 10-Q prose path already established.
- Verdict flows through the existing `fundamental` analyst stream unchanged.

### Plan 2 — News subsystem rebuild (ticker level)
- Raze and rebuild `src/agents/analysts/news/` internals; the branch shape
  (`Fetch → per-ticker fan-out → Joiner`) and its wrappers stay.
- **Per-ticker news-history store** of article embeddings (reuses the memory
  embedding client), giving a deterministic **staleness pre-filter**
  (Tetlock similarity measure) that replaces the ~800-line heuristic
  reranker in `fetch.py`. Only novel, material articles reach the LLM.
- Prompt decision rule reframed from "react to sentiment now" to **surprise
  classification + drift positioning** (are we inside a drift window; is
  the news fresh; direction and expected persistence).
- `AnalystVerdict` gains a **`horizon`** field (trading days the lean is
  expected to hold) so the strategist and the 5d technical signal align.
- Backtest PIT-correctness: the history store is rebuilt from the golden
  cache's news timeline during replay — never persisted across windows.

## 6. Plans 3–4 — linkage subsystem (full design)

### 6.1 Architecture

```
/company-news (existing fetch, refetched cache)
        │
   [Specificity router]  (fetch-time, deterministic)
        ├── company-specific ──► per-ticker News branch (Plan 2)
        └── roundup / multi-company ──► macro stream
                                            │
                                [Staleness pre-filter]  (embeddings)
                                            │  novel articles only
                                    [Event digester]  (1 flash call/tick)
                                            │  normalised events
                      ┌─────────────────────┤
               [Event registry]       [Matcher]  (1 flash call/tick,
               (SQLite, decay)            │       reads exposure map)
                      └──► active-drift context ──► linkage verdicts
                                                        │
                                              strategist digest
                                          (new `linkage` analyst stream)
```

### 6.2 Plan 3 — macro stream plumbing (deterministic, no LLM)

- **Router.** `_score_article_specificity` in `news/fetch.py` stops being a
  discard filter and becomes a **router**: company-specific articles to the
  per-ticker branch (as now); multi-company/roundup/market-summary articles
  to the macro stream, tagged with the watchlist tickers they mention.
  (Note: main's `a46f14e` currently demotes roundups to score 0 — that
  logic is the router's classification input, inverted from "bin" to
  "route".)
- **One shared model.** Everything remains a `NewsArticle`; the macro stream
  is a routing destination, not a new schema.
- **Cache refetch.** `backtest_fetch` refetches news for target windows so
  cached responses include the roundups previously discarded at the cap
  margin. Live and backtest consume the identical endpoint with identical
  routing — apples-to-apples by construction.

### 6.3 Plan 4 — linkage analyst

- **Staleness pre-filter.** Embedding similarity against a `macro` namespace
  of Plan 2's news-history store; threshold in `config/analysts.json`.
- **Event digester.** One flash-class call per tick over surviving novel
  articles → events: `(summary, category ∈ {macro, sector, merger},
  entities, surprise_direction, novelty)`. Closed-vocab where possible.
- **Exposure map.** Per-ticker channels: sector, commodity sensitivities,
  geographies, key customers/suppliers, regulatory exposure. Built by one
  deeper-model pass per ticker; persisted as a data artefact; refreshed on
  watchlist change plus a weekly staleness cap. Never rebuilt on the tick
  path.
- **Matcher.** One flash-class call per tick: active events × exposure map →
  per-ticker `AnalystVerdict` under new analyst name **`linkage`** (added to
  `AnalystName`), carrying `horizon`. No exposed tickers → no verdicts,
  logged explicitly as a valid quiet tick.
- **Event registry.** SQLite via the existing persistence layer:
  `(event_id, summary, category, tickers, direction, event_date,
  horizon_days, source_article_ids)`. Events expire past their horizon; the
  matcher receives active events so drift windows persist consistently
  across ticks. Same primitive Plan 2 uses for PEAD windows.
- **Strategist wiring** (the known three-place change): `context_shim`
  indexes `linkage_evidence`; `DEFAULT_ANALYST_WEIGHTS` in `contract.digest`
  gains `linkage`; strategist prompt describes the stream.

### 6.4 Rejected alternatives (recorded)

- *Linkage folded into the news verdict* — rejected: the scoreboard could
  not evaluate the channel in isolation, making the eval gate unanswerable.
- *Strategist-level macro brief* — rejected: the literature edge is
  ticker-specific exposure, and it bypasses the scoreboard.
- *Per-tick full-reasoning mapper* — rejected on token economics; exposure
  facts are stable and belong in the cached map.
- *Stateless event recompute per tick* — rejected: re-spends tokens and
  produces inconsistent event identity across ticks.
- *General-news feed as mapper input* — rejected under D2 (parity).
- *GDELT backfill* — rejected: only ~3 months guaranteed via DOC API,
  titles-only payload, new-provider normalisation cost.

## 7. Error handling

Loud failures throughout (established house rule):

- Provider/fetch errors **raise** — no silent empty macro stream.
- Digester and matcher distinguish "no fresh events" (valid, logged, quiet
  tick) from "call failed" (raises through the existing retry/isolation
  wrappers).
- Registry writes are transactional with the tick.
- Exposure-map staleness beyond the refresh cap fails the tick rather than
  silently matching against an outdated map.

## 8. Testing

- **Unit:** router determinism on fixture articles; staleness threshold
  behaviour; registry decay/expiry; digester and matcher schema validation
  with stubbed LLMs; filing-pair selection (Plan 1); horizon field
  propagation (Plan 2).
- **Integration:** backtest smoke on a short window asserting linkage
  verdicts **appear with positive signal** (assert presence, not merely
  absence of error — silent-degradation guard).
- **Eval:** scoreboard run with `linkage` as a first-class analyst in the
  (ticker, window) clustering; `iran-conflict-2026-02` (refetched) is the
  natural stress window.

## 9. Risks

- **Thin, partially-arbitraged edge** on a megacap watchlist — evals may be
  underpowered; a null result on megacaps is ambiguous (see §2).
- **LLM training-knowledge look-ahead** in backtests, most acute for the
  exposure map. Mitigation: build the map from filing/sector facts where
  possible; treat pre-cutoff eval results with suspicion either way.
- **Roundup coverage is an equity-news lens on macro** — genuinely global
  events appear only as their equity-market echo. Accepted trade (D2).
- **Company-news retention (~1 year)** bounds how far back refetched
  windows can reach; `baseline-2025-09` is near the margin and may thin out.

## 10. Out of scope (backlog candidates)

- Capture-only archive job for the Finnhub general feed (option value for a
  future, properly backtestable macro window). Deliberately not built
  pre-deployment.
- GDELT or other macro-news enrichment.
- Watchlist expansion toward mid-caps to strengthen drift-effect power.
- Fading stale news as a contrarian signal (Tetlock reversal) — Plan 2
  builds the staleness measure; the contrarian trade is a separate
  experiment.
