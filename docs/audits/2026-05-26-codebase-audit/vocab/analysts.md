# Vocabulary inventory — analysts

Scope: `src/agents/analysts/`. Inventory is taken from the analysts module's own reads/writes — keys that other modules write but analysts only consume are tagged "(read-only here)". Suspected dedupes flagged inline.

## State keys — read

| Key | Site | Notes |
|---|---|---|
| `tickers` | every fetch/joiner | watchlist for the tick (read-only here) |
| `tick_id` | every joiner | tick identifier (read-only here) |
| `as_of` | every fetch/joiner | tick timestamp; always coerced via `resolve_as_of` |
| `temp:technical_data` | `technical/agent.py` | per-domain working dict |
| `temp:social_data` | `social/agent.py` | per-domain working dict |
| `temp:news_data` | `news/joiner.py` | per-domain working dict |
| `temp:fundamental_data` | `fundamental/joiner.py` | per-domain working dict |
| `smart_money_data` (bare) | `smart_money/agent.py` | **policy deviation** — see F-analysts-006 |
| `temp:news_context_<TICKER>` | `news/per_ticker.py` (via ADK injector) | per-ticker prompt context |
| `temp:fundamental_context_<TICKER>` | `fundamental/per_ticker.py` | per-ticker prompt context |
| `temp:news_context` | `news/fetch_agent.py` | aggregate (unused downstream? candidate for cull) |
| `temp:fundamental_context` | `fundamental/fetch_agent.py` | aggregate (same question) |
| `temp:news_verdict_<TICKER>` | `news/joiner.py` | per-ticker LLM output, consumed by joiner |
| `temp:fundamental_verdict_<TICKER>` | `fundamental/joiner.py` | per-ticker LLM output |
| `temp:_obs_news_call_<TICKER>` | `news/joiner.py` | per-branch observability record |
| `temp:_obs_fundamental_call_<TICKER>` | `fundamental/joiner.py` | per-branch observability record |
| `temp:_obs_news_retries` | `news/joiner.py` | retry counter map |
| `temp:_obs_fundamental_retries` | `fundamental/joiner.py` | retry counter map |
| `temp:report_cache_hit_<analyst>_<ticker>` | `cache_callbacks.py` | report cache hit marker |

## State keys — write

| Key | Writer | Notes |
|---|---|---|
| `technical_verdicts` | `technical/agent.py` | canonical §A key (state_delta) |
| `technical_evidence` | written via evidence_writer (callback) | canonical §A key |
| `social_verdicts` | `social/agent.py` | canonical §A key |
| `social_evidence` | callback | canonical §A key |
| `news_verdicts` | `news/joiner.py` | canonical §A key |
| `news_evidence` | `news/joiner.py` | canonical §A key (atomic with news_verdicts) |
| `fundamental_verdicts` | `fundamental/joiner.py` | canonical §A key |
| `fundamental_evidence` | `fundamental/joiner.py` | canonical §A key (atomic) |
| `smart_money_verdicts` | `smart_money/agent.py:153` | **Rule 1 violation** (direct write, see F-analysts-005); also currently never invoked per F-001 |
| `temp:<domain>_data`, `temp:<domain>_context_<T>`, `temp:<domain>_verdict_<T>` | fetch_agent / per_ticker | working keys (see read column) |
| `temp:_obs_*` keys | per_ticker callbacks + joiners | observability scratch space |

## Schema fields used

From `contract.evidence`:
- `AnalystVerdict { lean, magnitude, confidence, rationale, key_factors, is_no_data, report? }`
- `TickerVerdict { ticker, lean, magnitude, confidence, rationale, key_factors, is_no_data, report? }`
- `VerdictBatch { verdicts: list[TickerVerdict] }`
- `AnalystEvidence { analyst, ticker, tick_id, recorded_at, verdict, features, feature_warnings }`
- `LlmTickerVerdict` — LLM output schema for News and Fundamental (extends TickerVerdict with `AnalystReport`).

Field-level notes:
- `rationale` — populated by deterministic analysts; LLM analysts leave empty and emit a `report` block instead. See §3.2 cluster 8 prose-field proliferation.
- `is_no_data` — synthesised by joiners and `_common.make_evidence_callback` when LLM omits a ticker.
- `report.summary`, `report.drivers[*]` — LLM-only fields.

## Config keys

From `config/analysts.json` via `config.analysts.get_analysts_config()`:
- `output_caps.report_summary_max_chars`
- `output_caps.report_driver_name_max_chars`
- `output_caps.report_driver_body_max_chars`
- `output_caps.rationale_max_chars`
- `models.news`, `models.fundamental` (model names per analyst)
- `llm_caps.<analyst>.{max_tokens, temperature, top_p, retries}`

From `config/heuristics.json` via `agents.analysts.heuristics.load_heuristics()`:
- `technical.*` (TechnicalHeuristics fields)
- `social.*` (SocialHeuristics fields)
- `smart_money.*` (SmartMoneyHeuristics fields — currently dead per F-001)
- `fundamental_vocab.*` (FundamentalVocabulary)
- `news_vocab.{catalysts, novelty, direction}` (NewsVocabulary)
- `golden_set.*` (GoldenSetConfig)

Env-var toggles read by analysts:
- `STOCKBOT_TERMINAL_LOG` — enables `emit_analyst_summary` row
- `STOCKBOT_TRACE` — toggles `_trace_maybe` writes (read indirectly via the helper)
- LLM observability toggle (read inside `make_observability_callbacks`)

## Internal verbs

Verbs / nouns used inside the module to describe domain concepts:

- **branch** — one per-ticker LLM agent (post Phase 9 fan-out)
- **fan-out / fan-in** — ParallelAgent over tickers; JoinerAgent consolidating
- **joiner** — agent that consolidates per-ticker results into canonical contract keys
- **per-ticker fetch / per-ticker prompt** — Phase 9 isolation pattern
- **branch failed** — sentinel for IsolatedFailureWrapper-caught exception; surfaces as `no_data` verdict
- **synthesise** — joiner's fabricate-no-data-verdict path
- **closed vocabulary** — fixed tag set the LLM may emit in `key_factors`
- **catalyst / novelty / direction / material** — News closed-vocab categories
- **report cache hit / miss** — `cache_callbacks` outcomes; hit short-circuits LLM call
- **prompt_version** — hex digest derived from prompt-builder source bytes (see report_cache.py)
- **observability call record** — per-branch latency/token dict on `temp:_obs_*`
- **vocab** — pydantic-validated vocabulary instance passed to prompt builders
- **extractor / features** — deterministic feature dict per analyst (separate channel from LLM verdict)
- **golden set** — labelled prompt-eval set (heuristics layer, not actively wired here)
- **prompt-facing cap vs schema cap** — two-tier convention described in `news/prompts.py` (LLM told one bound, schema enforced separately for deterministic path)

### Suspected vocabulary duplication

| Term A | Term B | Where | Comment |
|---|---|---|---|
| `temp:news_data` | `temp:fundamental_data` (+ bare `smart_money_data`) | per-domain working dict | identical role; only domain differs (smart_money also breaks the `temp:` prefix rule) |
| `temp:<domain>_context_<T>` | `temp:<domain>_context` | fetch agents | per-ticker vs aggregate; the aggregate may be unused — worth a grep before keeping |
| `NewsJoinerAgent` / `FundamentalJoinerAgent` | — | structural mirrors (F-analysts-007) |
| `NewsFetchAgent` / `FundamentalFetchAgent` | — | structural mirrors (F-analysts-008) |
| `_build_news_per_ticker_branch` / `_build_fundamental_per_ticker_branch` | — | structural mirrors (F-analysts-009) |
| `build_news_branch` / `build_fundamental_branch` | — | structural mirrors (F-analysts-010) |
| `no verdict from LLM` no-data synthesis | three sites | F-analysts-015 |
| `branch_failed` / `*_fetch failed` warning strings | five fetch sites | inconsistent format makes log-grep brittle |
