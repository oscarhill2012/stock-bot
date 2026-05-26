# Module audit — analysts

Audit target: `src/agents/analysts/` and the tests exercising it.
Authoritative source: `docs/audits/2026-05-26-codebase-audit/intent.md` §7.

---

## F-analysts-001
- **Category:** policy-mismatch
- **Severity:** P0
- **Location:** `src/orchestrator/pipeline.py:82-93` (consumes analysts module) and the entire `src/agents/analysts/smart_money/` tree
- **Evidence:** Intent §7.1 (authoritative) says: *"Smart-money analyst status: Registered in `AnalystPool` and runs every tick. Emits a canonical no-data shape ... Downstream consumers may assume the key exists. Any defensive code that handles `smart_money_evidence` absence is dead."* The actual pipeline disagrees:
  ```python
  # src/orchestrator/pipeline.py:82-93
  return ParallelAgent(
      name="AnalystPool",
      sub_agents=[
          parallel_deterministic,
          fundamental_branch,
          news_branch,
          # _build_smart_money_analyst(h.smart_money) — shelved (see docstring).
          # Re-enable by re-importing _build_smart_money_analyst above and
          # appending it here once notable_holders / politician trades have
          # working PIT-correct providers.
      ],
  )
  ```
  Smart_money is **commented out** of the pool. It never runs. `smart_money_evidence` is **never produced**.
- **Intent violated:** §7.1 directly. The audit's own authoritative resolution is contradicted by the source the audit is reading.
- **Suggested action:** investigate. Human must choose: (a) re-enable smart_money in the pool so §7.1 holds, or (b) revise §7.1 to "shelved/unwired" and then file follow-ups marking the entire `src/agents/analysts/smart_money/` directory + `smart_money_*` evidence-consumer code as dead. The current state is the worst of both worlds: a 184-line live module that downstream code defensively reads but pipeline never invokes.
- **Notes:** Because every other finding below depends on which arm of this fork the human picks, this is the load-bearing P0 to resolve first. The findings that follow assume the *code reality* (smart_money is NOT in the pool); they should be re-scoped if the human picks arm (a).

## F-analysts-002
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/analysts/smart_money/agent.py` (entire file, 184 lines), `src/agents/analysts/smart_money/fetch.py` (entire file, 143 lines), `src/agents/analysts/smart_money/__init__.py`, plus the heuristics section `SmartMoneyHeuristics` in `heuristics.py:59-66`.
- **Evidence:** No production import of `_build_smart_money_analyst` or `smart_money_analyst` exists:
  ```
  $ rg -n "_build_smart_money_analyst|smart_money_analyst[^_]" src/ scripts/
  src/orchestrator/pipeline.py:88:            # _build_smart_money_analyst(h.smart_money) — shelved
  src/orchestrator/pipeline.py:89:            # Re-enable by re-importing _build_smart_money_analyst above
  src/agents/analysts/smart_money/__init__.py:3:__all__ = ["smart_money_analyst"]
  src/agents/analysts/smart_money/agent.py:165:smart_money_analyst = SmartMoneyAnalyst(...)
  src/agents/analysts/smart_money/agent.py:168:def _build_smart_money_analyst(...)
  ```
  Only references are the module-internal definition, the `__init__.py` re-export, and commented-out pipeline lines. All other uses are tests.
- **Intent violated:** code-reality only. Depends on F-analysts-001 resolution.
- **Suggested action:** delete (conditional on F-analysts-001 resolving as "stay shelved").
- **Notes:** If retained, fix the Rule 1 violation in F-analysts-005 and the bare-key state issue in F-analysts-006 before re-enabling.

## F-analysts-003
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/analysts/test_smart_money.py`, `tests/unit/test_smart_money_fetch.py`, `tests/unit/test_smart_money_gate.py`, `tests/unit/test_derive_smart_money_verdict.py`, `tests/agents/memory/test_writer_smart_money_seen.py`
- **Evidence:** All exercise smart_money production code that the pipeline never invokes (per F-analysts-001/002). `tests/analysts/test_smart_money.py` is purely smoke (asserts class identity, no behaviour); `test_smart_money_fetch.py` exercises the never-called `smart_money_fetch_callback`; `test_writer_smart_money_seen.py` synthesises `smart_money_evidence` rows that the live pipeline cannot produce.
- **Intent violated:** intent §7.2 dead-test pattern: "tests that exercise dead code". Same conditionality as F-analysts-002.
- **Suggested action:** delete (conditional on F-analysts-001 → "stay shelved").
- **Notes:** Extractor tests under `tests/unit/contract/extractors/test_smart_money.py` and the model test `tests/unit/data/models/test_smart_money.py` are about the contract layer, not analyst wiring — leave to the contract module's audit.

## F-analysts-004
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/agents/analysts/report_cache.py:547-582` — `log_cache_hit_to_state`
- **Evidence:** Self-documented no-op:
  ```python
  def log_cache_hit_to_state(state, *, analyst, ticker, input_hash, originating_as_of) -> None:
      """No-op — audit drains report-cache hits from ``obs/logs/`` since S3.
      ...
      """
      return None
  ```
  Still called from `cache_callbacks.py:216` and `cache_callbacks.py:80` imports it. The function body does nothing; every call is a no-op.
- **Intent violated:** n/a — internal cleanup.
- **Suggested action:** delete the function and its single call site at `cache_callbacks.py:216-222`. The docstring already explains the structured log at `cache_callbacks.py:224-234` is the source of truth.
- **Notes:** Removes one of two API surfaces in the cache module that has no behavioural effect.

## F-analysts-005
- **Category:** policy-mismatch
- **Severity:** P1
- **Location:** `src/agents/analysts/smart_money/agent.py:153`
- **Evidence:**
  ```python
  state["smart_money_verdicts"] = verdicts
  ...
  # No events emitted — pure state mutation, same as TechnicalAnalyst.
  return
  yield  # required to make this an async generator
  ```
  The comment claims "same pattern as TechnicalAnalyst" but Technical *does* yield a `state_delta` event (`technical/agent.py:150-154`); only smart_money writes the verdict key directly without `state_delta`. Contract §C-Rule 1: "All writes to session state must go through `EventActions(state_delta=...)`." Smart_money violates this.
- **Intent violated:** `docs/contract-invariants.md` §C-Rule 1; intent.md §A row for `smart_money_verdicts` (would say analogous to `technical_verdicts` row line in §A schema table).
- **Suggested action:** refactor (yield an Event with `state_delta={"smart_money_verdicts": verdicts}`) if the analyst is kept; otherwise moot once F-analysts-002 deletes it.
- **Notes:** Comment is a documentation bug too — TechnicalAnalyst no longer writes directly.

## F-analysts-006
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/agents/analysts/smart_money/fetch.py:135` and `src/agents/analysts/smart_money/agent.py:117`
- **Evidence:** Smart_money writes the raw-data working dict to the bare key `state["smart_money_data"]`, not `temp:smart_money_data`. Every other analyst uses `temp:`-prefixed keys (`temp:technical_data`, `temp:social_data`, `temp:news_data`, `temp:fundamental_data`). Per contract §C-Rule 2, invocation-scoped working data should be `temp:`-prefixed so ADK strips it at the boundary.
- **Intent violated:** §C-Rule 2 in `docs/contract-invariants.md`; pattern divergence flagged by intent.md §A.
- **Suggested action:** rename to `temp:smart_money_data` (if smart_money is retained per F-analysts-001).
- **Notes:** Also referenced from `src/backtest/decision_logger.py:321` and `src/orchestrator/state.py:81`; both would need to follow the rename.

## F-analysts-007
- **Category:** dedupe-candidate
- **Severity:** P0
- **Location:** `src/agents/analysts/news/joiner.py` (150 lines) and `src/agents/analysts/fundamental/joiner.py` (161 lines). Pair of joiner agents.
- **Evidence:** The fundamental joiner module-docstring literally says *"This is a symmetric mirror of NewsJoinerAgent (`news/joiner.py`) with every `news` identifier replaced by `fundamental`."* Side-by-side, the only differences are:
  - `temp:news_verdict_<T>` vs `temp:fundamental_verdict_<T>` state-key prefix
  - `temp:news_data` vs `temp:fundamental_data` data-key prefix
  - `extract_news_features` vs `extract_fundamental_features`
  - `"news"` vs `"fundamental"` analyst label
  - emitted keys `news_verdicts`/`news_evidence` vs `fundamental_verdicts`/`fundamental_evidence`
  - `02_news_verdict` vs `02_fundamental_verdict` trace label
  - `temp:_obs_news_call_<T>` / `temp:_obs_news_retries` vs fundamental equivalents.
  All control flow (synthesise-on-missing, validate `TickerVerdict`, run extractor, build `AnalystEvidence`, observability roll-up, single yield) is identical.
- **Intent violated:** §3.2 cluster 2 (`{domain}_verdicts` vs `{domain}_evidence`) — the parallelism extends to the joiners. Risk of divergent paths is explicit (intent §A footnote calls these out as Rule 4 keys).
- **Suggested action:** consolidate-with-X. Factor into one parameterised `JoinerAgent(analyst_name, data_key, verdict_key_template, extractor, output_keys, trace_label)` factory. Saves ~150 LOC and removes a maintenance trap.
- **Notes:** This is the single most expensive divergence risk in the analysts module — two structurally identical 150-line files held in sync by hand. If a fix lands in news/joiner but not fundamental/joiner (or vice versa), the analyst output silently drifts.

## F-analysts-008
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** `src/agents/analysts/news/fetch_agent.py` (121 lines) and `src/agents/analysts/fundamental/fetch_agent.py` (210 lines)
- **Evidence:** Fundamental's docstring says *"The design mirrors `agents.analysts.news.fetch_agent.NewsFetchAgent` exactly."* Both implement: iterate `state["tickers"]`, fetch domain data (one call for news, three for fundamental), serialise to dicts, build per-ticker formatted context blocks, write `temp:<domain>_data` + `temp:<domain>_context_<T>` for each ticker + `temp:<domain>_context` aggregate, trace, yield one event.
  Differences: the inner fetch (news has one `get_stock_news` call; fundamental has three calls — `get_company_ratios`, `get_company_filings`, `get_insider_trades` — plus a `Form4Bundle` type-guard), and the formatter (`_build_ticker_news_context` vs `_build_ticker_fundamental_context`).
- **Intent violated:** internal cleanup; no contract violation.
- **Suggested action:** consolidate-with-X. Extract a `PerTickerFetchAgent(name, domain_label, fetch_fn, context_formatter)` parameterised by the domain-specific fetch and formatter. The per-ticker iteration, serialisation, payload assembly, and yield are mechanical.
- **Notes:** Lower severity than F-007 because the inner fetch differs in count (1 vs 3 calls) so the factory needs a list-of-fetches abstraction. Worth doing because F-007 + F-008 + F-009 + F-010 share the same Phase-9 cookie-cutter shape and need consolidating together.

## F-analysts-009
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** `src/agents/analysts/news/per_ticker.py` (192 lines) and `src/agents/analysts/fundamental/per_ticker.py` (205 lines)
- **Evidence:** Fundamental's docstring says *"This module is the Fundamental mirror of `agents.analysts.news.per_ticker`; the two are kept structurally symmetric so they evolve in lock-step."* Both implement the same shape: build instruction (replace `{ticker}` and `{<domain>_context}`), wire cache callbacks via `make_report_cache_callbacks` (already factored), wire observability + trace callbacks conditionally on env vars, chain them, read LLM caps, construct `LlmAgent` with `output_schema=LlmTickerVerdict` + `output_key=temp:<domain>_verdict_<T>`, wrap in `RetryingAgentWrapper`, wrap in `IsolatedFailureWrapper`. Only inputs are: `analyst_name`, `model_name`, `instruction_builder`, `vocab`, `hash_inputs`, `llm_caps`, `prompt_version`, trace-label prefix.
- **Intent violated:** internal cleanup.
- **Suggested action:** consolidate-with-X. One `build_per_ticker_branch(*, analyst_name, ticker, vocab, instruction_builder, hash_inputs, prompt_version, llm_caps, model, trace_prefix)` factory; news and fundamental shrink to ~15-line thin wrappers.
- **Notes:** This is the deepest dedupe in the analysts directory — `make_report_cache_callbacks` was already factored from these two files; the surrounding LlmAgent + wrappers + chained callbacks is now the next layer that wants factoring.

## F-analysts-010
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/agents/analysts/news/agent.py:26-83` and `src/agents/analysts/fundamental/agent.py:33-89`
- **Evidence:** Both `build_news_branch` and `build_fundamental_branch` are 60-line factories that assemble `SequentialAgent[FetchAgent, ParallelAgent[per-ticker branches], JoinerAgent]`. The list comprehension building the per-ticker branches, the `ParallelAgent` wrap, and the `SequentialAgent` wrap are mechanical and identical. Only differences: analyst label in agent names, fetch class, joiner class, per-ticker builder, vocab type.
- **Intent violated:** internal cleanup.
- **Suggested action:** consolidate-with-X. If F-007/008/009 are consolidated, this collapses naturally into one `build_llm_analyst_branch(analyst_name, fetch_cls, joiner_cls, per_ticker_factory, vocab, tickers)`.
- **Notes:** Don't address in isolation — only worth it as part of the F-007 to F-010 dedupe pass.

## F-analysts-011
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** singleton-vs-factory parallelism in `src/agents/analysts/{technical,social,smart_money}/agent.py` and their `__init__.py` re-exports
- **Evidence:** Every deterministic analyst module exports both a module-level singleton (e.g. `technical_analyst = TechnicalAnalyst(heuristics=load_heuristics().technical)` at `technical/agent.py:159`) and a factory (`_build_technical_analyst()` at `technical/agent.py:162`). Production uses only the factory:
  ```
  $ rg -n "from agents.analysts.technical import|technical_analyst" src/ scripts/
  src/orchestrator/pipeline.py:53: from agents.analysts.technical.agent import _build_technical_analyst
  src/orchestrator/pipeline.py:66:    _build_technical_analyst(h.technical),
  src/agents/analysts/technical/agent.py:159: technical_analyst = TechnicalAnalyst(...)
  src/agents/analysts/technical/agent.py:162: def _build_technical_analyst(...)
  src/agents/analysts/technical/__init__.py:3: __all__ = ["technical_analyst"]
  ```
  Only `tests/analysts/test_technical.py` and `tests/analysts/test_smart_money.py` import the singleton, and the only assertions they make are `isinstance(x, BaseAgent)` and `x.name == "..."`. Same pattern for social. Smart_money singleton chain is dead per F-002.
- **Intent violated:** n/a — internal cleanup.
- **Suggested action:** delete the module-level singletons and the `__init__.py` re-exports. Rewrite the two trivial test assertions to use the factory.
- **Notes:** Singletons are construction-time side-effects (call `load_heuristics()` at import) that exist only to keep two two-line tests working. Removing them simplifies the module API and removes import-order coupling.

## F-analysts-012
- **Category:** silent-failure
- **Severity:** P2
- **Location:** `src/agents/analysts/news/fetch_agent.py:80-92`, `src/agents/analysts/fundamental/fetch_agent.py:113-163`, `src/agents/analysts/smart_money/fetch.py:107-133`, `src/agents/analysts/technical/fetch.py:65-85`, `src/agents/analysts/social/fetch.py:55-73`
- **Evidence:** Every fetch site has the same pattern:
  ```python
  try:
      articles = await get_stock_news(ticker, as_of=as_of)
  except Exception as exc:  # noqa: BLE001 — degrade gracefully per ticker
      _LOGGER.warning("news fetch failed for %s: %s", ticker, exc)
      articles = []
  ```
  Empty-list fallbacks (and `None` for ratios) flow downstream into extractors and joiners which then synthesise no-data verdicts. Per `docs/test-policy.md §A.7`: *"`is_no_data=True`, empty news/insider/verdict lists, and `neutral` fallback verdicts must be asserted **against** in the happy path."*
- **Intent violated:** Per-ticker graceful degradation is the intended behaviour (intent §2.1, "Every per-ticker LLM branch is wrapped by `IsolatedFailureWrapper` so a branch failure logs but does not abort the tick"). The fetch-level swallow predates the IsolatedFailureWrapper pattern and overlaps with it — the wrapper would now catch the same exceptions further out. This is design-by-belt-and-braces.
- **Suggested action:** investigate. Two choices: (a) raise from the fetch and let `IsolatedFailureWrapper` handle it (cleaner; only one swallow point); (b) keep the inner swallow but add an explicit warning-count metric so the happy path can assert zero. Today nothing distinguishes "all five tickers had real news" from "all five tickers failed silently and returned empty articles".
- **Notes:** Linked to test-gap F-analysts-013.

## F-analysts-013
- **Category:** test-gap
- **Severity:** P2
- **Location:** No test asserts the happy-path fetch produced non-empty data — covers all five analyst fetch sites listed in F-012.
- **Evidence:** Per `docs/test-policy.md §G.8`: *"`branch_failed` warnings are not benign ... Pipeline-level tests should `caplog.set_level(WARNING)` and assert no `branch_failed` record was emitted on the happy path."* Searching: no test under `tests/analysts/` or `tests/integration/` asserts the *absence* of `"_fetch failed"` warning lines:
  ```
  $ rg -n "fetch failed|branch_failed" tests/ | grep -v ".pyc"
  (no analyst-fetch happy-path assertions)
  ```
  The integration tests at `tests/integration/test_analyst_pool.py` and `tests/integration/backtest/test_end_to_end_smoke.py` either stub the leaf fetches or do not assert against the warning record set.
- **Intent violated:** `docs/test-policy.md` §A.7 and §G.8.
- **Suggested action:** add a happy-path test for each analyst (or one pool-level test) that uses `caplog.set_level(WARNING)` and asserts no `*fetch failed*` record was emitted.
- **Notes:** Same shape as the "silent failures are recurring" user feedback recorded in CLAUDE.md.

## F-analysts-014
- **Category:** over-abstraction
- **Severity:** P2
- **Location:** `src/agents/analysts/report_cache.py:59-219` — auto-derived prompt-version constants + `_load_prompt_builders` filesystem-loader trick
- **Evidence:** The module uses `importlib.util.spec_from_file_location` to load `news/prompts.py` and `fundamental/prompts.py` by *file path* at import time, bypassing the canonical Python import system, to compute two short hex strings (`NEWS_PROMPT_VERSION`, `FUNDAMENTAL_PROMPT_VERSION`). The docstring explains the circular-import that necessitates the workaround and notes *"A cleaner long-term fix would be to drop the eager `from .agent import news_analyst` from the news + fundamental package `__init__.py` files."* See `report_cache.py:117-121`.
- **Intent violated:** n/a — internal cleanup.
- **Suggested action:** investigate. Per the source comment, deleting `from .agent import build_news_branch` from `news/__init__.py:13` (and the fundamental equivalent) breaks the cycle — then the filesystem-loader contortion in `report_cache.py:90-180` can be replaced with normal imports. (Combined with F-011, the eager imports in `__init__.py` lose their last reason to exist.)
- **Notes:** Net effect: delete ~90 lines of importlib gymnastics plus the singletons in F-011.

## F-analysts-015
- **Category:** dedupe-candidate
- **Severity:** P3
- **Location:** Three places synthesise the "no-data" `AnalystVerdict`:
  - `src/agents/analysts/_common.py:148-158`
  - `src/agents/analysts/news/joiner.py:66-76`
  - `src/agents/analysts/fundamental/joiner.py:77-87`
- **Evidence:** All three construct:
  ```python
  AnalystVerdict(lean="neutral", magnitude=0.0, confidence=0.0,
                 rationale="no verdict from LLM", key_factors=[], is_no_data=True)
  ```
  (Plus matching `ticker_verdict = TickerVerdict(ticker=ticker, **verdict.model_dump())` in the joiners.)
- **Intent violated:** §3.2 cluster 8 prose-field proliferation; intent.md no-data flag semantics.
- **Suggested action:** consolidate-with-X. Provide a single `AnalystVerdict.no_data(reason: str = "no verdict from LLM")` classmethod (or module-level helper). Eliminates drift risk if the canonical no-data shape ever changes.
- **Notes:** Could live in `contract/evidence.py` next to the schema.

## F-analysts-016
- **Category:** test-gap
- **Severity:** P2
- **Location:** No test asserts the `_strategist_validation_callback` / `StrategistEnricher` discriminator path inside analysts evidence flow. (Cross-module note; analysts module flagged because joiners produce both the LLM `TickerVerdict` shape and the deterministic `AnalystEvidence` shape from the same per-ticker key.)
- **Evidence:** Per intent §3.2 cluster 2, `{domain}_verdicts` and `{domain}_evidence` are both written per analyst per tick. The joiners write both atomically in one `state_delta` event. There is no test asserting the two keys remain *consistent* (same set of tickers, no `verdict` in evidence row that disagrees with the corresponding ticker_verdict). A drift between the two would silently break the strategist's evidence view (`context_shim.py:243`).
- **Intent violated:** intent §3.2 cluster 2.
- **Suggested action:** add a consistency assertion to the joiner unit tests (`tests/analysts/news/test_joiner.py`, `tests/analysts/fundamental/test_joiner.py`).
- **Notes:** Low-cost test, high value because the dedupe in F-007 will reshape these joiners.

---

## Counts

| Category | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|
| policy-mismatch | 1 | 1 | 1 | 0 | 3 |
| dead-code | 0 | 2 | 0 | 0 | 2 |
| dead-test | 0 | 1 | 0 | 0 | 1 |
| dedupe-candidate | 1 | 2 | 2 | 1 | 6 |
| silent-failure | 0 | 0 | 1 | 0 | 1 |
| over-abstraction | 0 | 0 | 1 | 0 | 1 |
| test-gap | 0 | 0 | 2 | 0 | 2 |
| **Total** | **2** | **6** | **7** | **1** | **16** |
