# Provider Research — Agent Template

You are one of 15 parallel research subagents for the **StockBot** project — a
pre-deployment AI stock trading bot (Python + Google ADK + Trading 212). The
team has tried **twice before** to nail down data providers and both attempts
produced confused or incomplete output. **This is the third attempt — your
report must be precise, fully-templated, and verified against current docs.**

---

## 1. Project shell conventions

Do NOT prepend `cd "/home/oscarhill2012/Documents/Repository/StockBot" && ...`
to Bash commands.  The Bash tool already runs in the project root.  Compound
`cd && ...` invocations break the permission allowlist and force manual
approval on every call.  Run commands directly.

---

## 2. Your scope

ONE row from the procurement list — the row passed in your invocation prompt.
Do NOT research other rows; they have their own agents.  The full Section 7
table below is for **cross-cutting awareness only** — if you notice that a
provider you research could also serve another row, NOTE IT in your report's
cross-cutting section, but **do not investigate that other row**.

---

## 3. Key constraints (from `docs/data-and-providersv2.md` Section 9 — Decisions)

- **Strict FREE-ONLY for first backtest.** Paid is acceptable later, not now.
- **Target 50 tickers, minimum 20, end-goal 100** (paid acceptable at 100).
- **Daily bars, 1 tick/day cadence.**
- **Backtest harness requires PIT correctness** — no forward-looking leaks.
  The existing PIT cache (`src/backtest/cache/`) handles per-(ticker, as_of)
  caching, but the **provider itself** must support historical date queries.
- **Python ecosystem** — prefer providers with Python SDKs but REST/HTTP
  works.
- **Trading 212 is the execution layer** (not relevant to data providers, but
  you may see it referenced).

---

## 4. Full procurement context (Section 7 — cross-cutting awareness only)

| # | Data type | Fields needed | Lookback | PIT | Used by | Priority |
|---|---|---|---|---|---|---|
| 1 | Daily OHLCV (per ticker) | timestamp, open, high, low, close, volume, adjusted | 2y | trivial | Technical | must-have |
| 2 | Daily OHLCV (SPY + sector ETFs) | same as #1 | 2y | trivial | Technical | must-have |
| 4 | Options summary | iv_rank, atm_iv_30d, put_call_ratio | snapshot | medium | Technical | nice-to-have |
| 5 | Company ratios — full set | trailing_pe, forward_pe, peg, revenue_growth_yoy, profit_margin, debt_to_equity, roe, fcf, market_cap, beta, dividend_yield, analyst_rating_avg, last_price | snapshot | hard (XBRL PIT) | Fundamental | must-have |
| 6 | Latest earnings | report_date, eps_actual, eps_estimate, revenue_actual, revenue_estimate, guidance_text | last 4 quarters | trivial | Fundamental | must-have |
| 7 | SEC filings prose excerpts | form_type, filed_at, mda_excerpt, risk_factors_excerpt, body_excerpt (8-K) | last 3 per form | trivial | Fundamental | must-have |
| 8 | Insider trades (Form 4) | side, shares, price_per_share, insider_name, insider_title, transaction_code, is_10b5_1, footnote, transaction_date, filed_at | 30d | trivial | Fundamental | must-have |
| 10 | Analyst consensus & targets | target_high/low/mean, recommendation_mean, recent_revisions[] | snapshot | hard | Fundamental | nice-to-have |
| 11 | Short interest | short_interest, days_to_cover, settlement_date | 90d | trivial | Fundamental | nice-to-have |
| 12 | News articles | headline, summary, url, source, published_at, sentiment, topic, relevance_to_ticker, cluster_id | 7d, ≤20/ticker | trivial | News | must-have |
| 13 | Social sentiment | platform, mention_count, positive_score, negative_score; 30d baseline | snapshot + 30d baseline | impossible-cheap | Social | must-have-if-free |
| 14 | Politician trades | politician, chamber, party, side, transaction_date, disclosure_date, amount_min_usd, amount_max_usd | 30d (by disclosure) | medium | SmartMoney | must-have |
| 15 | SC 13D / 13G filings | holder, form_type, intent, is_amendment, filed_at, accession_no | 90d | trivial | SmartMoney | must-have |
| 16 | 13F quarterly holdings | fund_name, ticker, shares, value, change_vs_prior_quarter | last 2 quarters | medium (45d lag) | SmartMoney | nice-to-have |
| 17 | Form 144 planned sales | insider_name, planned_shares, planned_date, filed_at | 30d | trivial | SmartMoney | nice-to-have |

---

## 5. Process

1. **Identify 3+ candidate providers** for YOUR row.
2. For each candidate, use the **context7 MCP tools** first:
   - If you don't have the context7 tool schemas loaded yet, call
     `ToolSearch` with query `"context7"` (max_results: 5) to load them.
   - Then use `mcp__plugin_context7_context7__resolve-library-id` to find
     the library's context7 ID.
   - Then use `mcp__plugin_context7_context7__query-docs` to fetch the
     specific facts you need (free tier limits, endpoints, field shape, PIT
     support).
   - If context7 does not have the library, fall back to `WebSearch` plus
     `WebFetch` against the provider's official docs.
3. **Verify actual field coverage** — do not trust marketing copy; check the
   API response schema, sample call output, or the provider's data
   dictionary.  Match returned fields against the field list YOUR row needs.
4. **Verify PIT support specifically** — does the API let you query "what
   would I have seen on YYYY-MM-DD"?  Some providers serve "current
   snapshot only" which breaks backtests.  Be explicit about *how* historical
   queries work (date-range params? archive endpoints? snapshot only?).
5. **Note free-tier limits precisely** — requests per minute, per day,
   ticker cap, history depth, any commercial-use restrictions in the ToS.
6. **Note any cross-cutting potential** — does this provider also serve
   other rows in Section 7?  Flag it in the cross-cutting section.

---

## 6. Output

Write your full report to
`docs/superpowers/specs/provider-research/row-<N>.md` using the `Write`
tool.  Use this EXACT structure (the synthesis pass depends on consistency
across all 15 reports):

````markdown
# Row #<N>: <description>

## Summary

One paragraph: which providers are viable, which is not, headline finding.

## Candidate 1: <Provider Name>

- **Docs URL:** <link>
- **Free tier limits:** <req/min, req/day, ticker cap, history depth — be precise>
- **Paid tier reference:** <starting price + what tier unlocks>
- **Fields covered vs needed:**
  - Covered: <list — match against fields YOUR row needs>
  - Missing: <list>
- **PIT support:** <yes/no/partial — explain HOW (date-range param? snapshot only? cache forward?)>
- **API shape:** <REST/SDK/GraphQL, auth method, Python lib name if exists>
- **Historical date-range support:** <yes/no — how far back, how queried>
- **Gotchas:** <known issues, rate limit footguns, format quirks, ToS concerns>
- **Cross-cutting:** <other Section 7 rows this provider could also serve, if any>

## Candidate 2: <Provider Name>
(same template)

## Candidate 3: <Provider Name>
(same template — add a fourth or fifth if genuinely relevant)

## Recommendation

- **Best free option:** <name> — <one-line why; if none, say "no free option viable" and explain>
- **Best paid fallback (for end-goal 100 tickers):** <name> — <one-line why>
- **PIT verdict:** <can we backtest this row PIT-correctly on the free tier? yes / no / with-caveats — explain>

## Notes for synthesis

<anything cross-cutting; weird findings; any provider that serves multiple rows; anything the synthesis agent needs to make a cross-row decision>
````

---

## 7. Do not

- Research rows other than your assigned row.
- Make up provider information — verify everything via docs.
- Skip or reorder sections of the output template (synthesis depends on
  consistent shape across all 15 reports).
- Write to any file other than `docs/superpowers/specs/provider-research/row-<N>.md`.
- Modify any project source code — this is research-only.
- Commit anything.  The orchestrator will commit after the synthesis pass.

---

## 8. Final response

When done, return a one-line confirmation to the orchestrator:
`"Row #<N> report written: <brief headline finding>"`.
