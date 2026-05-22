# Spec-prep grounding — surgical correctness + foundational thesis-memory

**Date:** 2026-05-22
**Purpose:** Single handoff doc consumed by two parallel Claude Code sessions that each brainstorm one spec without losing the rich context built up in the originating session.

---

## How to use this document

This document is the result of a long analysis-and-grounding session that:

1. ran three subagents to analyse `backtests/baseline-2025-09/runs/first-test/` (computational, market, LLM) and produced three reports under `backtests/baseline-2025-09/runs/first-test/report/`
2. walked every proposed fix against `docs/contract-invariants.md` to determine whether it respects, extends, or questions the canonical tick-boundary contract
3. partitioned the work into three specs (surgical / foundational / enrichment) with a clear sequencing rationale

Two parallel sessions consume this doc:

- **Session A — Surgical correctness spec.**
  Brainstorm D1 (news report block drop root cause) and D2 (fundamental bull-trigger redesign), then draft a single combined spec covering S1–S8 + D1 + D2 for one-hit execution. Filename: topic-keyed, no date prefix (per the memory convention).

- **Session B — Foundational thesis-memory spec.**
  Brainstorm the §E persistence implementation for `positions` + `thesis` rows, the replacement of the `derivation.py:192-200` carry-forward with explicit re-evaluation, and the strategist-prompt redesign that uses prior-tick thesis. Draft the spec for foundational thesis persistence + re-evaluation only.

The third spec — **enrichment** — covers `memory_buffer` + `day_digest` and is sequenced after Spec B has bedded in. It is sketched here for boundary clarity but is **not** the focus of either parallel session.

Each session should read this doc first and only this doc; everything load-bearing is captured below. The three analysis reports and `docs/contract-invariants.md` are pointed to where the session genuinely needs to dig further.

---

## Background — what the backtest run revealed

Run: `backtests/baseline-2025-09/runs/first-test/`, git sha `5e3d38a`, 46 / 60 ticks (interrupted 2025-10-02), 20-stock watchlist, strict-mode ON, baseline window 2025-09-02 → 2025-10-13.

**Headline:** bot returned +0.73 % vs SPY +4.04 % (vs-SPY −3.31 pp; `report/metrics.md` overstates the gap as −4.55 pp — likely a SPY-anchor backfill bug). 22 % of capital concentrated into 3 names (AVGO / MSFT / XOM) on tick 1, 78 % parked in cash for the whole window, the other 17 watchlist tickers never re-evaluated.

**Headline diagnostic:** strategist rationale text is byte-identical across all 46 ticks; `held_view_at_decision: null` on every decision. The strategist is being asked to deliberate from a flat portfolio every tick — there is no cross-tick context, the §A `positions` row has no persistence layer behind it (§E unimplemented), the prompt frames every tick as a fresh start, and `derivation.py:192-200` carries un-picked tickers forward at 0.0 weight forever.

**Three reports live at:**

- `backtests/baseline-2025-09/runs/first-test/report/analysis_computational.md` — PIT correctness, plumbing, contract integrity (9 KB)
- `backtests/baseline-2025-09/runs/first-test/report/analysis_market.md` — why we underperformed SPY (6 KB)
- `backtests/baseline-2025-09/runs/first-test/report/analysis_llm.md` — LLM output failures and their causes (24 KB)

Each session should consult its relevant report; this doc summarises the actionable findings but the reports have the per-tick evidence.

---

## The three-spec partition

The originating session partitioned the findings into three specs after the user pushed back on conflating foundational with enrichment memory. The partition:

| Spec | Scope | Sequencing |
|---|---|---|
| **A — Surgical correctness** | S1–S8 + D1 + D2 (this doc) | Ships first. Independent of memory design. Removes traps that would corrupt foundational the moment it lands. |
| **B — Foundational thesis-memory** | §E persistence for `positions` + `thesis` rows; D3 carry-forward → explicit re-evaluation; strategist prompt redesign to use prior thesis | Brainstorm + spec in parallel with A's execution. Ships immediately after A. First meaningful backtest is gated on this. |
| **C — Enrichment memory** | §E persistence for `memory_buffer` + `day_digest`; learning behaviours; regime context; cross-position pattern memory | Followup, after B has bedded in. Not the focus of either parallel session. |

### Why three specs, not one

- **Surgical (A) is independent.** None of S1–S8 / D1 / D2 constrain memory design — they're correctness, observability, and input-quality fixes that need fixing regardless. Bundling them with foundational would slow them down for no design benefit.
- **Foundational (B) is bare minimum for meaningful backtest.** Without thesis persistence + portfolio awareness + re-evaluation, every backtest is a one-shot tick-1 decision in disguise. The user's explicit framing: *"these are the foundational memory systems we need to even run backtests surely."*
- **Enrichment (C) is bolt-on.** Learning, patterns, regime context — adds value but doesn't gate backtest meaningfulness. Sequencing it after B bedding in lets us learn what experiential memory actually needs to do before designing it.

### Foundational vs enrichment categorisation (user's reframe, verbatim)

> *"the memory system we implement is to enable learning from past... being aware of the portfolio, not restarting portfolio each tick, and ability to generate a new stance for a new tick are all bare minimum requirements, these are the foundational memory systems we need to even run backtests surely... where as the memory system we add ontop is to enrich decision making"*

Mapping to §E:

- **Foundational ≡ thesis memory (per-position).** §E paraphrased: *"For each open position: why the bot entered, what it expected to happen, what would invalidate the thesis, and what would confirm an exit. Read by the strategist when considering exits. Keyed by ticker / position id. Lives from entry to exit."* Bound to §A `positions` + `thesis`.
- **Enrichment ≡ experiential memory (cross-position).** §E paraphrased: *"Patterns from past trades, daily observations, regime context. Read by the strategist when considering new entries and contextualising the world. Time-ordered, bounded retention, probably summarised."* Bound to §A `memory_buffer` + `day_digest`.
- **Re-evaluation is not memory at all.** It's strategist semantics — replacing `derivation.py:192-200` carry-forward with an explicit re-evaluation pass over every watchlist ticker each tick. Lives with foundational because it's a pre-condition for thesis memory to be useful (memory keyed on "current stance" sees {AVGO, MSFT, XOM} only if 17 tickers are silently zero'd forever).

---

## Spec A — Surgical correctness (this spec)

### Status legend

- **R** — Respects an invariant we extend
- **E** — Extends an area the contract is silent on
- **Q** — Puts the contract itself under question (none in this spec)

### S1 — `reference_prices` PIT-clamp at Phase 2

**Anchor.** §A `reference_prices` row: *Lifetime = tick-scoped, Source of truth = bulk yfinance pull, Refresh point = Phase 2.* §B Phase 2: *"Tick-scoped fields are populated fresh from their Source of truth (clock, config, broker, bulk data pull)."*

**Today.** Seeded **once at Phase 1** (run-start) with `end=window.end`. That breaks §B Phase 2: it's a run-start cache with future bars baked in, masked downstream by a re-clamp in the technical extractor.

**Evidence.** Every `audit/*.tick.json` for tick 1 has SPY / XLK / XLF / XLE / XLV / XLY / XLP / XLI / XLB / XLRE / XLU / XLC entries with `max_ts = 2025-10-13T00:00:00` (= `window.end`) while `as_of = 2025-09-02T13:30`. Tripwire `any_filter_key_after_as_of` fires once on tick 1.

**Code citations.**
- `src/backtest/runner.py:469-473` — `_seed_reference_prices(store=store, window_start=window.start, window_end=window.end)` populates `state["reference_prices"]` with bars up to `window.end`
- `src/backtest/runner.py:64-107` — body of `_seed_reference_prices`; no `as_of` cap
- `src/contract/extractors/technical.py:128-135` — defence-in-depth re-clamp at consumer side

**Status.** **R**. Fix should move the seed into the Phase 2 boundary (per tick), or PIT-clamp by `as_of` at seed time. Preference: Phase 2 placement — the contract says Phase 2, so put it in Phase 2.

**Live-safe.** Yes — Phase 2 fires every tick in both lifecycles; live cold-starts it from yfinance same as backtest.

### S2 — Executor `del positions[ticker]` only on true close

**Anchor.** §A `positions` row note: *"The thesis book. Per-position entry rationale + exit basis. Distinct from `portfolio` (broker truth) — `positions` is strategist intent."*

**Today.** `executor/agent.py:156` deletes the thesis on any SELL, even 1 % trims. With `MAX_DELTA_PER_TICKER = 0.01`, the thesis is wiped before the position is actually closed → contradicts the §A note.

**Code citations.**
- `src/agents/executor/agent.py:97` — `TradeLogRow` only written on SELLs that close a position (currently triggered on every SELL, which means a `TradeLogRow` is also written on every 1 % trim — likely a second symptom of the same bug)
- `src/agents/executor/agent.py:156` — `del positions[order.ticker]` on every SELL regardless of remaining quantity
- `src/agents/executor/agent.py:202-210` — already-correct state_delta yield for `positions` (Rule 1 is satisfied; the bug is purely the bookkeeping rule)

**Status.** **R**. Fix: only `del positions[ticker]` when broker-remaining-quantity == 0 (queried via `broker.get_portfolio()` post-fill, or computed from prior `state["portfolio"]` minus `fill.quantity`). Only write `TradeLogRow` on true close.

**Live-safe.** Yes — pipeline code; Trading212Broker and FakeBroker both expose the broker interface used here.

**Note.** Because §E persistence is not yet implemented, this fix does **not** make positions cross-tick-durable in live. It makes the in-tick bookkeeping correct so that when §E lands (Spec B), the thesis it persists is not mid-trim corrupted.

### S3 — `_report_cache_hits_for_audit` via state_delta or obs/logs

**Anchor.** §C Rule 1: *"All writes to session state must go through `EventActions(state_delta=...)`. Direct mutation is not durable on real session backends."* §C Rule 8: *"Observability is additive and contract-neutral."*

**Today.** `agents/analysts/report_cache.py:579` does `state.setdefault("_report_cache_hits_for_audit", []).append(...)` from inside per-ticker sub-agents. Driver drains it at `driver.py:310`. The in-tick callback carve-out (added 2026-05-20) does **not** apply — these are full BaseAgents, not `after_agent_callback`s.

**Evidence.** Audit cache_hits sums to 26 across all 46 ticks; structured-log cache_hit events sum to 469. Live would see 0 / 469 because the direct mutation is silently dropped on real session backends.

**Code citations.**
- `src/agents/analysts/report_cache.py:579` — the direct mutation
- `src/backtest/driver.py:310` — the drain

**Status.** **R**. Two equally-valid fixes:
- Yield audit hits via `state_delta` from each per-ticker sub-agent (proper Rule 1 conformance)
- Have the audit reader consume `obs/logs/` directly, the way the rest of the audit subsystem does (Rule 8 permits this — observability is additive)

The second is cheaper and removes a Rule 1 hot spot rather than fixing it; preference is the second.

**Live-safe.** Yes — and currently broken in live, not just backtest.

### S4 — Span-name prefix bugs in `reporting.py`

**Anchor.** §C Rule 8 — observability is contract-neutral.

**Today.** `reporting.py:581, 590` use exact `==` against `"generate_content"` / `"invoke_agent"`. ADK emits `"generate_content <model_id>"` and `"invoke_agent <agent_name>"`. Token counters always 0; per-agent latency always blank.

**Evidence.** `report/metrics.md` line 13: *"LLM tokens — input 0, output 0, total 0 across 0 model calls"*. Trace file shows 42 spans named `"generate_content gemini-2.5-flash-lite"` + 1 `"generate_content gemini-2.5-pro"`, all carrying `gen_ai.usage.input_tokens` / `output_tokens` attributes.

**Code citations.**
- `src/backtest/reporting.py:581` — `if name == "generate_content":` exact-match bug
- `src/backtest/reporting.py:590` — `if name == "invoke_agent":` same bug
- `src/backtest/reporting.py:95` — `fill_count = len(trade_rows)` related issue: counts closed round-trips only, displayed as "Total fills: 3" when there were 135 broker fills (compounds with S2 — the over-eager `del` is what makes this look "closed" 58 times)

**Status.** **R**. `name.startswith(...)`. Also read `gen_ai.agent.name` attribute rather than parsing suffix for the agent name. Rename "Total fills" to "Closed round-trips" or count opens as well.

**Live-safe.** N/A — backtest-only artefact, §D1 carve-out.

### S5 — Insider `.model_dump()` + decision_logger strict serialiser

**Anchor.** §C Rule 8.

**Today.** `fetch_agent.py:165-169` stores the `Form4Bundle` Pydantic instance directly; sibling fields use `.model_dump()`. `decision_logger.py:136` `default=str` stringifies it into a 2.3 KB Python repr.

**Evidence.** `decisions/2025-09-15T13-30-00p00-00__MSFT__buy.json` — `analyst_inputs.fundamental.insider` is a 2 292-char string starting `"trades=[InsiderTrade(ticker='MSFT', insider_name='Satya Nadella', …"`.

**Code citations.**
- `src/agents/analysts/fundamental/fetch_agent.py:165-169` — `fundamental_data[ticker]["insider"] = insider_bundle` (no `.model_dump()` call)
- `src/backtest/decision_logger.py:136` — `json.dumps(snapshot, indent=2, default=str)`
- `src/backtest/decision_logger.py:25-33` — `_coerce` function; only handles top-level coercion, not nested Pydantic models in lists / dicts

**Status.** **R**. Two-line fix at `fetch_agent.py`: `.model_dump()`. Tighten `decision_logger.py`: replace `default=str` with a recursive serialiser that errors loudly on un-dumpable types so the next regression isn't silent.

**Important.** The LLM is NOT exposed to this — analysts read formatted text from `temp:fundamental_context_<TICKER>`. Tick-level signal is fine; future RAG corpus is what's corrupted.

**Live-safe.** Yes — first fix is pipeline (lifecycle-symmetric); second is §D1 backtest-only but harmless to live.

### S6 — `decision_tag` enum {entry, ramp, trim, exit, hold_flat}

**Anchor.** §A `strategist_decision` row: tick-scoped output; content shape is uncontracted.

**Today.** `decision_tag` is the constant string `"catalyst_driven_entry"` across all 46 ticks regardless of whether the decision is an opening BUY, a 1 % ramp, a trim, a full exit, or a hold-flat.

**Status.** **E**. Derive the tag from prior-vs-new weight in `derivation.py` (or a post-hoc enrichment step). Categories must be sufficient for memory (Spec B / Spec C) to key on intent rather than action.

**Pre-condition for Spec B.** Any memory writer keyed on decision intent must see a discriminating tag — without S6, memory keyed on "decision_tag" sees `catalyst_driven_entry` for every row and can't distinguish entries from trims from holds.

**Live-safe.** Yes — pipeline code.

### S7 — Suppressed tick-1 strategist trace exception

**Anchor.** §C Rule 8.

**Today.** `observability/trace.py:163` `contextlib.suppress(Exception)` silently swallows the tick-1 `03_strategist` failure. The LLM did run (terminal log shows 38.6 s strategist call) but the trace dropped.

**Code citation.** `src/observability/trace.py:163`

**Status.** **R**. `logger.exception` inside the suppress so single-tick drops aren't invisible.

**Live-safe.** N/A — backtest trace writer, §D1.

### S8 — Tripwire renames

**Anchor.** §C Rule 8.

**Today.** Two tripwires fire benignly on every (relevant) tick, drowning out genuine signal:
- `midnight_utc_timestamps_seen` — 46 / 46 ticks; date-only sources promoted to midnight is steady state
- `open_tick_sameday_bar` — 23 / 23 open ticks; provider strips the same-day bar before consumer sees it

**Code citations.**
- `src/backtest/audit/telemetry.py:184` — `hour == 0 and minute == 0` check that fires the first tripwire
- `src/backtest/audit/tripwires.py:71-72` — tripwire definitions
- `src/backtest/providers/price_history_cache.py:92-93` — strips same-day bar before consumer sees it

**Status.** **R**. Rename to `*_advisory` or drop; document in the tripwires module why they're benign.

**Live-safe.** N/A — backtest audit, §D1.

### D1 — News report block drop (brainstorm needed)

**Anchor.** §A `news_verdicts` row: owned by `NewsJoinerAgent`, tick-scoped, content uncontracted.

**Symptom from `analysis_llm.md`.** News verdicts intermittently drop entire blocks of report content. Need to root-cause whether this is LLM output truncation, schema validation rejection, joiner consolidation losing data, or upstream cache miss.

**Brainstorm prompts (for Session A):**

1. Where in the news pipeline does the block disappear? Trace from `NewsFetchAgent` → per-ticker `temp:news_context_<TICKER>` → `NewsAnalyst_<TICKER>` LLM call → `NewsJoinerAgent` consolidation → final `news_verdicts` state key. At each boundary, what is the block shape and what could drop it?
2. Is it an LLM output-validation failure (truncated JSON, schema rejection)? Check `agents/llm_retry.py` recent changes — `_is_retryable` now classifies `pydantic.ValidationError` as retryable, which means validation failures are silently retried; what if all retries fail?
3. Is it a joiner consolidation bug — the joiner ignoring missing per-ticker keys instead of recording the gap?
4. The fix must respect §A `news_verdicts` content shape (per-ticker verdict dicts) and §C Rule 4 uniqueness. Should we add a "block dropped" verdict shape so the strategist sees the gap, rather than silently omitting the ticker?

**Status (post-brainstorm).** Likely **R** — fix lives in the news pipeline; contract-neutral.

### D2 — Fundamental `planned_sale_dominant` over-vetoes (brainstorm needed)

**Anchor.** §A `fundamental_verdicts` row: owned by `FundamentalJoinerAgent`, tick-scoped, content uncontracted.

**Symptom from `analysis_market.md`.** Single feature `planned_sale_dominant` (routine 10b5-1 insider sales) blocked TSLA / GOOGL / NVDA / AMD selection on tick 1 by tagging them bearish. The fundamental analyst was never bullish on any mega-cap across the 46-tick window. Routine 10b5-1 sales are not bearish in practice.

**Brainstorm prompts (for Session A):**

1. Where in `src/agents/analysts/fundamental/features.py` is `planned_sale_dominant` weighted? What is the threshold for "dominant"?
2. What's the bull-trigger shape that should fire for a mega-cap with revenue growth, margin expansion, and routine 10b5-1 sales? Is the current feature set even capable of generating a bullish verdict for AAPL / MSFT / GOOGL / etc., or are we structurally short bull-triggers?
3. Should `planned_sale_dominant` be excluded when the sales match a published 10b5-1 plan (i.e. pre-scheduled, not opportunistic)? Finnhub's insider data shape — does it carry the 10b5-1 flag?
4. The fix must respect §A `fundamental_verdicts` content shape and the per-ticker fanout (Phase 9). Bull-trigger changes should be in `features.py` derivation, not in the LLM prompt — the LLM should see the same feature shape with corrected weights.

**Status (post-brainstorm).** Likely **R** — fix lives in `features.py` weighting; contract-neutral.

### Items explicitly excluded from Spec A

These were considered and pushed out:

- **N1 — Strategist `state_delta` propagation** (was C1).
  Walking the code: every Rule-1 venue is already correct. Snapshotter, Executor, TechnicalAnalyst, SocialAnalyst, joiners, RiskGate, StrategistDecisionWriter, EvidenceWriter, MemoryWriter — all yield state_delta. Strategist's `_strategist_validation_callback` is covered by the in-tick callback carve-out (2026-05-20). The "frozen rationale" symptom is **design**, not plumbing — addressed by Spec B.

- **D3 — Carry-forward in `derivation.py:192-200`.**
  Tightly entangled with foundational thesis memory and prompt redesign. Pushed to Spec B.

- **§E persistence for `positions` + `thesis`.**
  The cross-tick survival of the thesis book. Pushed to Spec B.

- **§E persistence for `memory_buffer` + `day_digest`.**
  Experiential memory. Pushed to Spec C.

### Live-symmetry summary for Spec A

All Spec A fixes are lifecycle-symmetric:

| Fix | Lives in | Live-safe? | Why |
|---|---|---|---|
| S1 | Pipeline / lifecycle wrapper | ✓ | Phase 2 fires every tick in both lifecycles |
| S2 | `src/agents/executor/agent.py` | ✓ | Pipeline code; broker interface available in both |
| S3 | Analyst pipeline / observability | ✓ | Currently broken in live too; fix benefits both |
| S4 | `src/backtest/reporting.py` | N/A (§D1) | Backtest-only artefact |
| S5 | Pipeline (`fetch_agent.py`) + backtest logger | ✓ | First fix is pipeline-symmetric; second is §D1 |
| S6 | `derivation.py` | ✓ | Pipeline code |
| S7 | `src/observability/trace.py` | N/A (§D1) | Backtest trace writer |
| S8 | `src/backtest/audit/` | N/A (§D1) | Backtest audit |
| D1 | News pipeline | ✓ | Pipeline code |
| D2 | `features.py` weighting | ✓ | Pipeline code |

Nothing in this spec relies on in-process state survival between ticks. Cold-start one Cloud Run Job per tick, every fix still does the right thing.

### Reminder for Session A

- The user asked for **one combined spec** for one-hit execution covering S1–S8 + D1 + D2.
- Filename: topic-keyed, no date prefix (per memory `feedback_spec_filenames_no_dates`).
- Suggested location: `docs/superpowers/specs/<topic>.md` — pick a name that captures the surgical-correctness theme.
- Per the CLAUDE.md brainstorming→backlog convention: after the brainstorming session, propose appending any deferred ideas to `docs/superpowers/backlog.md`.

---

## Spec B — Foundational thesis-memory + re-evaluation

### Scope

1. **§E persistence subsystem** for the two thesis-related §A cross-tick rows: `positions` and `thesis`. The contract has §E as explicit followup; this spec implements that part of §E.
2. **D3 — Replace carry-forward semantics in `derivation.py:192-200`** with an explicit re-evaluation pass. Strategist must opine on every watchlist ticker every tick.
3. **Strategist prompt redesign** to use prior-tick thesis (per-position rationale, opened_price, opened_at, expected catalysts, invalidation conditions, exit basis). The "starting from a flat portfolio" framing must go.

### Out of scope (explicitly deferred)

- §E persistence for `memory_buffer` + `day_digest` — that's experiential memory (Spec C).
- Learning behaviours, regime context, cross-position pattern memory — Spec C.
- Any input-quality fixes (news drop, fundamental bull-triggers) — those are Spec A.
- Live broker target (Trading212 specifics) — out of scope; the persistence layer abstracts over the broker.

### Anchored to which contract sections

- **§A** `positions`, `thesis` rows — Owner = Strategist, Lifetime = cross-tick, Source of truth = Persistence layer (§E)
- **§B Phase 2** — Cross-tick fields populated from persistence at tick-start
- **§B Phase 4** — Cross-tick state_delta writes persisted before process exit / next tick
- **§C Rule 1** — All state writes via `EventActions(state_delta=...)`; cross-tick writes cannot use the in-tick callback carve-out
- **§C Rule 7** — Cross-tick persistence is the lifecycle's job, not the pipeline's. Pipeline reads from / writes to `state`; lifecycle wrapper bridges to persistence at Phase 2 + Phase 4
- **§E** — Cross-session persistence followup work (this spec implements thesis memory; experiential memory is Spec C)

### Open design questions to brainstorm (Session B)

The contract lists §E open questions explicitly. The ones in scope for Spec B (thesis only):

1. **Schema for thesis memory.** One row per open position. Fields suggested by §E: entry rationale, expected catalysts, invalidation conditions, exit criteria. Plus mechanical fields: `ticker`, `opened_at`, `opened_price`, `opened_tick_id`, `horizon`, `opened_tag`. Trigger for write: BUY that opens a new position (transitions `qty=0 → qty>0`). Trigger for delete or archive: SELL that closes to zero. Should we archive on close (for trade-log replay) or delete (and rely on `TradeLogRow` for history)?

2. **Live persistence target.** §E lists this as open. For Cloud Run Jobs (cold-start each tick), candidates: Firestore, Cloud SQL, GCS-backed SQLite. Brainstorm the trade-offs given that thesis memory is small (~10s of rows at any time), needs Phase 2 read + Phase 4 write per tick, and needs to survive cold start.

3. **Backtest persistence target.** §E suggests the existing per-run `runs/<run-id>/db.sqlite` SQLAlchemy store. Confirm or extend.

4. **Symmetric read / write contract.** §E requires: *"both lifecycles read from and write to the same persistence layer at the same lifecycle phases"*. What is the abstraction the pipeline sees? A `PersistenceLayer` protocol with `load_positions(as_of)` / `save_positions(state_delta)`? Where does it live (`src/orchestrator/` or `src/persistence/`)?

5. **Re-evaluation semantics for D3.** What does "explicit re-evaluation" look like? Options:
   - Strategist must produce a `TickerStance` for every watchlist ticker (no silent omissions)
   - LLM is shown the prior-tick stance for every ticker (so re-evaluation is grounded in prior intent)
   - LLM is asked explicitly "do you want to change this stance? if so, why?" for each ticker
   - The carry-forward at `derivation.py:192-200` is replaced with a validation that errors loudly if the LLM omits a ticker

6. **Strategist prompt redesign.** What does the prompt look like when it has prior thesis to reference? §A `thesis` is a "standing market thesis" — does the prompt show it back to the LLM and ask for diffs, or does it summarise into "what changed since last tick"? How does the prompt handle ticker-specific thesis (`positions`) vs market-wide thesis (`thesis`)?

7. **Phase 4 ordering.** Today's executor yields `state_delta` for `positions` with the post-trade book. Spec B must add: at Phase 4 the lifecycle wrapper reads that state_delta and writes to persistence. What is the ordering — executor yields, snapshotter yields, lifecycle wrapper drains? Where does the drain hook attach?

8. **Crash-recovery semantics.** What happens if a tick crashes between persistence write and process exit? Idempotency in §A `last_executed_tick_id` row guards re-runs, but what guards a partial persistence write?

### Backtest non-conformance to flag

Even though backtest is a long-lived process, it is **not** conformant to §B Phase 2: it sees prior `positions` because the dict survives in process, not because it was loaded from persistence. The Phase 2 invariant is *"populated from persistence ... Reading them from a leftover in-memory state dict is not permitted, regardless of lifecycle."* Spec B must close this even for backtest — the long-lived process must overwrite from persistence at Phase 2, not rely on the dict.

### Pre-conditions from Spec A

Spec B's value is gated on Spec A landing:

- **S2** — without it, the first 1 % trim wipes the thesis you just persisted. Memory becomes self-defeating.
- **S6** — without it, any memory writer keyed on decision intent sees `catalyst_driven_entry` for every row.
- **S1** — without it, any macro/benchmark memory consumer sees future bars.

Session B should brainstorm assuming Spec A is in flight; the spec text should declare the dependency.

### Code touchpoints (anticipated, brainstorm to confirm)

- `src/orchestrator/state.py` — `ticker_stances` table, possibly extended or supplemented
- `src/orchestrator/persistence.py` — likely the home for `load_positions` / `save_positions`
- `src/backtest/driver.py` — Phase 2 hydration hook, Phase 4 drain hook (backtest lifecycle)
- Live lifecycle wrapper (TBD — currently no live runtime exists per `project_stockbot_deployment_state`)
- `src/agents/strategist/derivation.py:192-200` — D3 carry-forward replacement
- `src/agents/strategist/prompts.py:60-67` — prompt redesign
- `src/agents/strategist/held_view.py` — render prior-thesis block from persisted state
- `src/agents/strategist/context_shim.py` — Phase 2 hydration into `temp:held_positions_view`
- `src/agents/strategist/agent.py` — possibly the `_strategist_validation_callback` to enforce stance-per-ticker

### Live-safety reminder for Spec B

This spec is the cross-tick persistence implementation. Live-safety is foundational, not optional. Every design choice must work for one-Cloud-Run-Job-per-tick cold-start lifecycle as well as the long-lived backtest lifecycle. The contract's §B / §C-Rule-7 / §D-non-carve-outs all bind here.

### Filename and convention

Topic-keyed, no date prefix. Final location: `docs/Phase10-post-first-backtest/specs/foundational-thesis-memory.md` (alongside Spec A under the same Phase 10 umbrella). Per the CLAUDE.md brainstorming→backlog convention: after the brainstorming session, propose appending any deferred ideas to `docs/superpowers/backlog.md` — especially anything experiential-memory shaped that surfaces, since Spec C will want it.

---

## Spec C — Enrichment memory (sketch only)

Not the focus of either parallel session. Sketched here so Spec A and Spec B sessions know what's deferred.

**Scope (future).**

- §E persistence subsystem for `memory_buffer` + `day_digest` cross-tick rows
- Experiential memory shape: time-ordered log, summarisation strategy, bounded retention, relationship between `memory_buffer`, `day_digest`, and `thesis`
- Learning behaviours: pattern extraction from past trades, regime context, day-level summarisation
- The strategist's reading of experiential memory when considering new entries vs exits

**Anchored to:** §A `memory_buffer` + `day_digest` rows; §E experiential-memory paragraph.

**Sequencing rationale:** after Spec B has shipped and the system can run meaningful backtests, we'll learn what experiential memory actually needs to do before designing it. Premature design = wrong abstractions.

---

## Cross-cutting reference — contract invariants summary

One-page summary of the load-bearing parts of `docs/contract-invariants.md`. Read the full doc when designing; this summary is for in-session lookup.

### §A — Field schema (rows that matter to these specs)

| Field | Owner | Lifetime | Source of truth | Refresh point |
|---|---|---|---|---|
| `tick_id` | Tick bootstrap | tick-scoped | wall clock | Phase 2 |
| `tickers` | Tick bootstrap | tick-scoped | `config/watchlist.json` | Phase 2 |
| `portfolio` | Broker | tick-scoped (state); cross-tick (broker reality) | Broker API | Phase 2 |
| `reference_prices` | Tick bootstrap | tick-scoped | Bulk yfinance pull | Phase 2 |
| `positions` | Strategist (via state_delta) | **cross-tick** | Persistence layer (§E) | Phase 2 read, Phase 4 write |
| `memory_buffer` | MemoryWriter (via state_delta) | **cross-tick** | Persistence layer (§E) | Phase 2 read, Phase 4 write |
| `day_digest` | MemoryWriter (via state_delta) | **cross-tick** | Persistence layer (§E) | Phase 2 read, Phase 4 write |
| `thesis` | Strategist (via state_delta) | **cross-tick** | Persistence layer (§E) | Phase 2 read, Phase 4 write |
| `strategist_decision` | Strategist (output_key) | tick-scoped | Strategist LLM call | Phase 3 |
| `technical_verdicts` / `social_verdicts` | corresponding BaseAgent (state_delta) | tick-scoped | analyst | Phase 3 |
| `fundamental_verdicts` / `news_verdicts` | joiner agent (state_delta) | tick-scoped | joiner | Phase 3 |

### §B — Phases

- **Phase 1 — Run-start (once per process).** Broker connection, config loaded, persistence layer ready, provider implementations wired. Live ≡ backtest.
- **Phase 2 — Tick-start (every tick).** Build state dict. Tick-scoped fields fresh from source of truth; cross-tick fields from persistence. Live ≢ backtest mechanically; Live ≡ backtest contractually.
- **Phase 3 — During-tick.** SequentialAgent runs. All state writes via state_delta (Rule 1). Pipeline reads from state only (Rule 7).
- **Phase 4 — Tick-end.** Cross-tick state_delta writes persisted. Broker called for executed trades. Observability flushed. Tick-scoped fields discarded.

### §C — Cross-cutting rules (the ones that matter here)

- **Rule 1** — State mutation rides on Events (`EventActions(state_delta=...)`). Direct dict mutation not durable on real session backends. **In-tick callback carve-out (2026-05-20):** `after_agent_callback`s may direct-write a state key whose only consumer is another agent in the same tick. Does NOT apply to cross-tick keys.
- **Rule 4** — ParallelAgent branches need unique `output_key`s (analyst pool has four distinct verdict keys).
- **Rule 7** — Cross-tick persistence is the lifecycle's job, not the pipeline's. Lifecycle wrapper bridges state to persistence at Phase 2 / Phase 4.
- **Rule 8** — Observability is additive and contract-neutral. Backtest reporting / trace writers / decision logger live here.

### §D — Additive carve-outs

- **D1** — Observability writes can differ between lifecycles
- **D2** — LLM stubbing in tests
- **D3** — Broker implementation (Trading212 vs FakeBroker)

**Non-carve-outs (must be identical):**
- Cross-tick state persistence
- State-dict shape
- Agent composition and ordering

### §E — Cross-session persistence (followup work)

- Two memory types: **thesis** (per-position) and **experiential** (cross-position)
- Spec B implements thesis memory
- Spec C will implement experiential memory
- Open design questions enumerated in §E (live persistence target, backtest persistence target, migration story, schema)

---

## Code citations index

Citations gathered during the analysis session, organised by file:

**`src/agents/executor/agent.py`:**
- `:42-44` — idempotency guard (`last_executed_tick_id` check)
- `:97` — `TradeLogRow` write on SELL
- `:156` — `del positions[order.ticker]` on every SELL (S2 target)
- `:202-210` — already-correct state_delta yield

**`src/agents/snapshot/agent.py`:**
- `:114-138` — exemplar state_delta pattern; copy-paste source for anywhere needing Rule 1 compliance
- `:126-131` — sibling state_delta fix pattern (already documented for `memory_buffer` / `day_digest` / `thesis`)
- `:132` — direct dict write (in-tick reader convenience)
- `:134-138` — yielded Event with state_delta (cross-tick durability)

**`src/agents/strategist/`:**
- `agent.py:84` — `_strategist_validation_callback` definition
- `agent.py:261` — `state["strategist_decision"] = decision_dump` direct write (covered by in-tick carve-out)
- `derivation.py:192-200` — carry-forward at 0.0 (D3 target)
- `prompts.py:60-67` — "starting from a flat portfolio" framing

**`src/agents/analysts/`:**
- `fundamental/fetch_agent.py:165-169` — `fundamental_data[ticker]["insider"] = insider_bundle` (S5 target)
- `report_cache.py:579` — direct state mutation from per-ticker sub-agents (S3 target)

**`src/backtest/`:**
- `runner.py:64-107` — `_seed_reference_prices` body
- `runner.py:469-473` — `_seed_reference_prices` call site (S1 target)
- `decision_logger.py:25-33` — `_coerce` function (S5 target)
- `decision_logger.py:136` — `json.dumps(snapshot, indent=2, default=str)` (S5 target)
- `reporting.py:95` — `fill_count = len(trade_rows)` (S4 related)
- `reporting.py:581` — `if name == "generate_content":` (S4 target)
- `reporting.py:590` — `if name == "invoke_agent":` (S4 target)
- `driver.py:310` — drains `_report_cache_hits_for_audit` (S3 related)
- `audit/telemetry.py:184` — midnight_utc tripwire source (S8)
- `audit/tripwires.py:71-72` — tripwire definitions (S8)
- `providers/price_history_cache.py:92-93` — strips same-day bar (S8)

**`src/contract/`:**
- `extractors/technical.py:128-135` — defence-in-depth ref_bars re-clamp (S1)

**`src/observability/`:**
- `trace.py:163` — `contextlib.suppress(Exception)` (S7 target)

**`src/orchestrator/`:**
- `state.py:9-13` — `ticker_stances` table (Spec B touchpoint)

---

## Gotchas / things to remember

1. **The contract is target-state, not current-code.** `docs/contract-invariants.md` describes what must be true; the code may not satisfy it yet. Audit findings from `analysis_computational.md` are gaps between code and contract.

2. **Backtest is non-conformant even today.** It's a long-lived process that happens to preserve `positions` between ticks in memory, but the contract (§B Phase 2) forbids that — cross-tick fields must come from persistence. Spec B closes this for backtest too, not just live.

3. **§E persistence is followup work in the contract.** The contract explicitly says: *"Until that subsystem exists, those rows describe target-state and any lifecycle that ships without true persistence for them violates the contract."* Spec B implements the thesis half of §E.

4. **In-tick callback carve-out (added 2026-05-20).** `after_agent_callback`s can direct-write a state key consumed by another agent in the same tick. The strategist's `_strategist_validation_callback` writing `strategist_decision` is the canonical case. **Does NOT extend to cross-tick keys.**

5. **Frozen rationale is design, not plumbing.** All state_delta writes are already correct. The byte-identical rationale across 46 ticks is the §E persistence gap + carry-forward + prompt framing — Spec B's problem.

6. **Per-ticker fanout is Phase 9.** `FundamentalAnalyst_<TICKER>`, `NewsAnalyst_<TICKER>`, with joiners. Verdict shape and unique-output-key requirement satisfied by `FundamentalJoinerAgent` / `NewsJoinerAgent` writing the canonical keys. See `docs/Phase9-agent-fanning-per-ticker/spec.md`.

7. **Shell convention (project CLAUDE.md):** do NOT prepend `cd "/home/oscarhill2012/..." && ...` to Bash commands. The Bash tool already runs in the project root. Use absolute paths or `git`-relative commands directly.

8. **Style (user-global CLAUDE.md):** British English everywhere (colour, behaviour, organisation, analyse, optimise). Comment the code — explain non-trivial logic. Function docstrings describing purpose, parameters, return. Whitespace for legibility.

9. **Brainstorming → backlog (project CLAUDE.md):** after any `/superpowers:brainstorming` session that produces a spec under `docs/superpowers/specs/`, **proactively propose** appending deferred ideas to `docs/superpowers/backlog.md`. Match the tiered format (Tier 1 / Tier 2 / Tier 3) with `**Origin:**`, `**The goal:**`, `**Key questions:**`, `**Dependencies:**`.

10. **Consequential decisions (user-global CLAUDE.md):** *"For big structural decisions ... assume I am wrong by default and require mutual agreement before proceeding."* Both sessions are designing consequential things; push back on the user where the design has flaws, don't just take instruction.

11. **graphify navigation (project CLAUDE.md):** `graphify-out/GRAPH_REPORT.md` + `graphify-out/graph_delta.md` are the structural index. Delta overrides report. After structural changes in either spec implementation, append to `graph_delta.md`.

12. **SPY return discrepancy (analysis_market.md §8).** `report/metrics.md` shows vs-SPY −4.55 % but recomputed from `db.sqlite::portfolio_snapshots` is −3.31 pp. Likely a forward-return-backfill anchor mismatch in `src/backtest/reporting.py`. Worth a one-line check; not in either spec's scope but flag if it lands in a related diff.

---

## Instructions for parallel sessions

Both sessions:

1. Open a fresh Claude Code in this repo (`/home/oscarhill2012/Documents/Repository/StockBot`).
2. Read this doc first: `docs/spec-prep/2026-05-22-spec-prep-grounding.md`.
3. Run `/superpowers:brainstorming` with the appropriate scope.
4. After brainstorming, draft the spec under `docs/superpowers/specs/<topic>.md` (topic-keyed, no date prefix).
5. After drafting, propose backlog appends per the CLAUDE.md convention.

**Session A** (this surgical spec):
- Brainstorm D1 (news report block drop) and D2 (fundamental bull-trigger redesign).
- Draft a single combined spec covering S1–S8 + D1 + D2 for one-hit execution.
- Spec name suggestion: `surgical-correctness-and-input-quality.md` or similar.
- The grounding in §§ S1–S8 + D1 + D2 above is the spec content skeleton; the brainstorm extends D1/D2.

**Session B** (foundational thesis-memory spec):
- Full brainstorm — this is greenfield design.
- Cover the eight design questions enumerated under "Open design questions to brainstorm (Session B)".
- Draft the spec under a name like `foundational-thesis-memory.md`.
- Pre-conditions on Spec A — declare the dependency in the spec text.
- Out of scope: experiential memory (`memory_buffer` / `day_digest`) — that's Spec C.

**Coordination:** the two sessions are independent. They share git state but should not touch each other's spec file. They share `docs/superpowers/backlog.md` — both can append at the end of their respective sessions. If both want to append simultaneously, second one rebases.

**End of handoff.**
