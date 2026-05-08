# graph_delta.md

## 2026-05-07 — Initial graphify run (full build)

First full graph built from scratch. 160 files processed: 147 code + 13 docs.

**Stats:** 939 nodes · 1,676 edges · 62 communities · 21 hyperedges

**Communities of note:**
- ORM Persistence & Equity Tracking (114 nodes) — largest cluster; `make_engine` and `BufferEntryRow` are hubs
- Data Aggregation Pipeline (98 nodes) — `get_stock_signal_bundle` as the single entry point for all data
- Agent Construction & Runtime (72 nodes) — factory functions + ADK base types
- Architecture Decisions & Principles (41 nodes) — design doc rationale nodes extracted and linked to concepts

**God nodes added:** FakeBroker (32 edges), BufferEntry (27), make_engine (25), Portfolio (24), Trading212Broker (23)

**Key hyperedges discovered:**
- Seven-stage HourlyTick sequential pipeline
- Four analysts executing in parallel via AnalystPool
- Broker Protocol pattern (Protocol + FakeBroker + Trading212Broker)
- Memory buffer eviction + compression pipeline
- Risk gate constraint application pipeline
- Paper trading validation loop (smoke_run → replay_backtest → plot_equity)

When graph_delta grows long, suggest user runs `/graphify . --update` to rebuild graph_report.

## 2026-05-07 — Codebase-wide comments + whitespace pass

Added docstrings, class comments, and blank-line spacing across all source files. No logic changed.

**Files updated:**
- `src/agents/analysts/technical/schema.py` — module docstring + class docstring
- `src/agents/analysts/fundamental/schema.py` — module docstring + class docstring
- `src/agents/analysts/sentiment/schema.py` — module docstring, field comments
- `src/agents/analysts/smart_money/schema.py` — module docstring, field comments, class docstring
- `src/agents/analysts/fundamental/agent.py` — module docstring, comment on singleton
- `src/agents/analysts/sentiment/agent.py` — module docstring, comment on singleton
- `src/agents/analysts/smart_money/agent.py` — module docstring, comment on singleton
- `src/agents/analysts/technical/fetch.py` — callback docstring, blank lines
- `src/agents/analysts/fundamental/fetch.py` — callback docstring, blank lines
- `src/agents/analysts/sentiment/fetch.py` — callback docstring, blank lines
- `src/broker/portfolio.py` — class docstrings, property docstrings, field comments
- `src/broker/protocol.py` — class docstrings, field comments
- `src/broker/fake.py` — class docstring, method docstrings, inline comments, blank lines
- `src/broker/trading212.py` — class docstring, method docstrings, inline comments
- `src/agents/risk_gate/constraints.py` — docstrings on all clamp functions + apply_constraints
- `src/agents/risk_gate/orders.py` — docstring on weights_to_orders, blank lines
- `src/agents/risk_gate/agent.py` — class docstring, inline comments, blank lines
- `src/agents/memory/schema.py` — class docstring, field comments on BufferEntry
- `src/agents/memory/writer.py` — class docstring, moved imports to top, whitespace
- `src/agents/strategist/schema.py` — class docstrings, field comments
- `src/agents/executor/agent.py` — class docstring, inline comments, aligned dict keys
- `src/agents/snapshot/agent.py` — class docstring, inline comments, aligned dict keys
- `src/orchestrator/state.py` — moved stray import to top, field comments, section headers
- `src/orchestrator/tick.py` — moved imports to top of main(), function docstring
- `src/orchestrator/persistence.py` — docstrings on all public functions
- `src/data/models/market.py` — class docstrings, field comments
- `src/data/providers/yfinance_stats.py` — docstring on `_f` helper

## 2026-05-08 — docs/data-and-providers.md added

New documentation file mapping current `StockSignalBundle` data contract to current + alternative providers. No code changes.

- New nodes: `docs/data-and-providers.md` (data contract reference + provider catalogue)
- Cross-links: references `src/data/models/bundle.py` (StockSignalBundle), `src/data/models/{market,filings,news,sentiment,trades}.py` (analyst data shapes), and the four `src/agents/analysts/*/fetch.py` callbacks

## 2026-05-08 — Analyst → Strategist contract spec (B1 / Goal 2)

Brainstormed design for the surface between analyst agents and the strategist. No code changes — spec + backlog only.

- New nodes: `docs/superpowers/specs/analyst-strategist-contract-design.md` (Goal 2 spec — hybrid analyst architecture, code-only digest, persisted as TickerEvidenceRow + AnalystEvidenceRow)
- Cross-links: references `src/agents/analysts/{technical,fundamental,sentiment,smart_money}/{schema,agent,prompts,fetch}.py`, `src/agents/strategist/{agent,schema,prompts}.py`, `src/orchestrator/persistence.py`, `src/data/models/*.py`
- Backlog: B1 entry removed (specced), Goal 2 line in Strategist Roadmap updated to link to spec, B2 substrate list updated with TickerEvidenceRow / AnalystEvidenceRow, B5 storage shape refined

## 2026-05-08 — Analyst → Strategist contract implementation plan

Plan doc only — no code yet. 19 TDD tasks across two PRs.

- New nodes: `docs/superpowers/plans/analyst-strategist-contract.md` (PR1 additive scaffolding tasks 1-11; PR2 wire-in + legacy retirement tasks 12-19)
- Anticipated future code structure (per plan): `src/contract/{types,digest,extractors/{technical,fundamental,sentiment,smart_money}}.py`, `src/config/digest.py`, new ORM rows `TickerEvidenceRow` + `AnalystEvidenceRow` in `src/orchestrator/persistence.py`, retirement of `src/agents/attribution/writer.py` + `AttributionSignalsRow`

## 2026-05-08 — Provider shell + registry refactor

Split src/data/providers/ into per-domain directories. Each provider is a
single async fetch() decorated with @register(domain, name, upstream,
rate_per_minute, burst). A new src/data/registry.py owns dispatch and the
shared limiter map. Active provider per domain is chosen in
config/data.json. `data.settings` is gone — secrets read via
data.secrets.require_key, non-secret config in DataConfig.

- New nodes: data.registry.register, data.registry.dispatch,
  data.registry._ensure_limiter, data.config.DataConfig,
  data.config.FetchDefaults, data.secrets.require_key,
  data.providers.<domain>.<provider>.fetch (×7).
- Changed nodes: data.aggregator.get_stock_signal_bundle now references
  domain getters only; data.models.bundle.ProviderError gains `domain`
  and the `provider` field stores the active provider name.
- Removed: data.settings.{Settings, get_settings, ProviderConfigError,
  require}; data.rate_limit.{FINNHUB, EDGAR, QUIVER, YFINANCE,
  ALL_LIMITERS, slowest_min_interval_seconds};
  data.MIN_DECISION_INTERVAL_SECONDS (replaced by
  data.registry.min_decision_interval_seconds()).
- Flat provider modules deleted: yfinance_stats.py, finnhub_news.py,
  finnhub_social.py, sec_filings.py, sec_holders.py, sec_insiders.py,
  quiver_politicians.py.
