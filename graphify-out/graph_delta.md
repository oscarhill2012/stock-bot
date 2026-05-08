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

## 2026-05-08 — config/ + scripts/ promoted to project root

Moved JSON config and CLI entrypoints out of `src/`. Project-root `config/`
now holds both `data.json` (already there) and `watchlist.json` (relocated
from `src/config/`); `src/config/` is gone. CLI scripts moved from
`src/scripts/` to `scripts/`.

- New nodes: `config/README.md` (config-file index + per-file setting tables)
- Moved nodes (path only):
  - `src/config/watchlist.json` → `config/watchlist.json`
  - `src/scripts/{__init__,hard_reset,init_db,initialise,plot_equity,replay_backtest,smoke_run,test_bundle}.py` → `scripts/<same>.py`
- Removed nodes: `src/config/` directory (now empty)
- Changed nodes:
  - `src/orchestrator/stock_picker.py` — `_WATCHLIST_PATH` resolves via `Path(__file__).resolve().parents[2] / "config" / "watchlist.json"` (project root) instead of `src/config/`
  - `scripts/initialise.py` + `scripts/hard_reset.py` — `--watchlist` default is now `config/watchlist.json`
  - `tests/unit/test_initialise_cli.py` — passes `--watchlist config/watchlist.json`
  - `pytest.ini` — `pythonpath = . src` (was `pythonpath = src`) so tests can import `scripts.<name>` from project root
  - `.claude/CLAUDE.md` — config + scripts conventions updated
  - `README.txt` — "Where to look next" lists `scripts/` + `config/`
- CLI invocation unchanged: `PYTHONPATH=src python -m scripts.<name>` still works because Python adds cwd to sys.path for `-m` (resolves `scripts.*` from root) while `PYTHONPATH=src` resolves `broker.*`, `orchestrator.*`, `lifecycle.*`, etc.
- Phase 4 Plan A note: the planned `src/config/digest.py` is left for that plan to decide — recommended to inline the constants in `src/contract/digest.py` (since they're behavioural defaults, not JSON config).

## 2026-05-08 — Phase 4 directory created; superseded specs/plans removed

Strategist v2 + Analyst→Strategist contract design + plan docs consolidated under `docs/Phase4-stratergist-and-analysts/` and re-sliced into four sub-plans (A → B → C → D) so each is invocable via `superpowers:subagent-driven-development`. No code changes yet — this is a docs reorg.

- New nodes: `docs/Phase4-stratergist-and-analysts/spec.md`, `plan-A-contract-scaffolding.md`, `plan-B-extractors-dual-emit.md`, `plan-C-strategist-v2.md`, `plan-D-cleanup.md`
- Removed nodes (docs): `docs/superpowers/specs/{strategist-council-design,exit-rules-and-telemetry-design,strategist-v2-design,analyst-strategist-contract-design}.md`, `docs/superpowers/plans/{strategist-council,exit-rules-and-telemetry,strategist-v2,analyst-strategist-contract}.md`
- Updated nodes: `docs/superpowers/backlog.md` — Goal 1 / Goal 2 entries replaced with a single Phase 4 pointer, B-tier dependency lines updated to reference Phase 4 plans
- Anticipated future code structure (carried forward into Phase 4 plans, unchanged): `src/contract/{types,digest,extractors/*}.py`, `src/config/digest.py`, `src/agents/contract/evidence_writer.py`, ORM rows `AnalystEvidenceRow` + `TickerEvidenceRow` + `TickerStanceRow` in `src/orchestrator/persistence.py`, retirement of `src/agents/attribution/writer.py` + `AttributionSignalsRow` (deferred to Plan D)
- `docs/superpowers/specs/` and `docs/superpowers/plans/` now contain only the `data-provider-shell` pair plus `backlog.md` — the strategist/contract track lives entirely under Phase 4

