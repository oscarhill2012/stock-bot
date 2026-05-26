# Contract module — vocab inventory

Scope: `src/contract/` + `src/agents/contract/`. Exhaustive list of every Pydantic model, field, validator, top-level constant, and helper. One line each. Type and meaning given where applicable.

---

## src/contract/__init__.py
- (empty file — package marker)

---

## src/contract/evidence.py

### Module-level constants / aliases
- `AnalystName` — `Literal["technical","fundamental","news","social","smart_money"]` — closed vocab of analyst identifiers.
- `_cfg` — `AnalystsConfig` instance from `config.analysts.get_analysts_config()` — import-time loaded.
- `_OUT` — `_cfg.output_caps` — prompt-facing char caps (alias for terser declarations).
- `_schema_cap` — `_cfg.schema_cap` — bound method that returns schema-cap (prompt cap × slack) per key.

### class `ReportDriver(BaseModel)`
- `name: str = Field(min_length=1)` — short label; max len NOT set (Vertex pad-target).
- `direction: Literal["bull","bear","neutral"]` — directional contribution.
- `weight: float = Field(ge=0.0, le=1.0)` — relative driver importance (drivers in a report should sum ~1.0; not enforced).
- `body: str = Field(min_length=1)` — prose explanation; no max_length.

### class `AnalystReport(BaseModel)`
- `summary: str = Field(min_length=1)` — connective-tissue paragraph; no max_length (Vertex pad-target).
- `drivers: list[ReportDriver] = Field(min_length=2, max_length=4)` — 2-4 drivers, prevents dilution.

### class `AnalystVerdict(BaseModel)`
- `lean: Literal["bullish","bearish","neutral"]` — directional call.
- `magnitude: float = Field(ge=0.0, le=1.0)` — "how far from neutral".
- `confidence: float = Field(ge=0.0, le=1.0)` — model self-confidence.
- `rationale: str = Field(default="")` — prose; LLM analysts no longer emit (default ""); deterministic extractors populate from tag list.
- `key_factors: list[str] = Field(default_factory=list, max_length=8)` — closed-vocab tags.
- `is_no_data: bool = False` — true ⇒ no signal this tick.
- `report: AnalystReport | None = None` — populated by LLM (and, per F-contract-001, also by deterministic extractors).
- **validator** `_report_required_when_data_present` (mode="after") — raises if `is_no_data=False and report is None`.

### class `TickerVerdict(AnalystVerdict)`
- `ticker: str` — extends AnalystVerdict with a ticker symbol.
- (inherits validator from AnalystVerdict.)

### class `LlmTickerVerdict(BaseModel)`
- `model_config = ConfigDict(extra="forbid")` — drift-protection.
- `ticker: str` — non-empty (enforced by validator below).
- `lean: Literal["bullish","bearish","neutral"]`
- `magnitude: float = Field(ge=0.0, le=1.0)`
- `confidence: float = Field(ge=0.0, le=1.0)`
- `is_no_data: bool` — REQUIRED (no default) — Vertex shortest-path defence.
- `key_factors: list[str] = Field(default_factory=list, max_length=8)`
- `report: AnalystReport` — REQUIRED (no Optional) — even when is_no_data=True.
- **validator** `_ticker_non_empty` — raises if ticker is empty string.

### class `VerdictBatch(BaseModel)`
- `verdicts: list[TickerVerdict] = Field(default_factory=list)` — wrapper for ADK `output_schema`.

### class `AnalystEvidence(BaseModel)`
- `ticker: str` — symbol.
- `analyst: AnalystName` — one of five canonical analyst names.
- `tick_id: str` — opaque per-tick id.
- `recorded_at: datetime` — UTC timestamp of assembly.
- `features: dict[str, float]` — extractor feature vector (numeric only).
- `feature_warnings: list[str] = Field(default_factory=list)` — extractor warnings; **DEAD: no producer (F-contract-010)**.
- `verdict: AnalystVerdict` — the analyst's call.
- `raw_text: str | None = Field(default=None, max_length=10_000)` — **DEAD: no producer (F-contract-002)**.

---

## src/contract/ticker_evidence.py

### class `AggregateVerdict(BaseModel)`
- `lean: Literal["bullish","bearish","neutral"]` — cross-analyst consensus direction.
- `magnitude: float = Field(ge=0.0, le=1.0)` — `|weighted signed-confidence|/total_weight`.
- `confidence: float = Field(ge=0.0, le=1.0)` — mean confidence of contributing (non-no_data) analysts.
- `disagreement: float = Field(ge=0.0, le=1.0)` — variance of signed confidences (clamped).
- `summary: str = Field(default="", max_length=240)` — rendered "3 bullish / 0 neutral / 1 bearish" string.

### class `TickerEvidence(BaseModel)`
- `ticker: str` — symbol.
- `tick_id: str` — per-tick id.
- `recorded_at: datetime` — UTC.
- `per_analyst: dict[str, AnalystEvidence]` — keyed by analyst name; always covers every key in `weights`.
- `aggregate: AggregateVerdict` — cross-analyst consensus.
- `weights: dict[str, float]` — snapshotted weights used by the aggregator.
- `last_price: float | None = None` — live close at build time; `None` AND `0.0` both mean "no price" (F-contract-013).

---

## src/contract/digest.py

### Functions
- `_lean_sign(lean: str) -> int` — bullish=+1, bearish=-1, neutral=0.
- `_fill_missing(per_analyst, ticker, tick_id, recorded_at, weights) -> dict` — neutral-fills every analyst in `weights` not present (F-contract-005).
- `_weighted_signed_confidences(per_analyst, weights) -> list[float]` — `weight × sign(lean) × confidence` per analyst; no_data→0.
- `_disagreement(per_analyst, weights) -> float` — variance of signed confidences (excluding no_data), clamped to [0,1].
- `_summary(per_analyst, weights) -> str` — renders "n bullish / m neutral / k bearish" lean breakdown.
- `_aggregate(per_analyst, weights) -> AggregateVerdict` — composes the AggregateVerdict from the helpers.
- `build_ticker_evidence(per_analyst, ticker, tick_id, recorded_at, weights, last_price=None) -> TickerEvidence` — **sole public entry-point** (Task A4).

---

## src/contract/digest_defaults.py

- `DEFAULT_ANALYST_WEIGHTS: dict[str, float]` — all five analysts → 1.0.
- `DIRECTION_DEAD_ZONE: float = 0.15` — magnitude below which lean collapses to "neutral".

---

## src/contract/strategist_prompt.py

### Module-level type
- `_BulletEntry = tuple[str, str, Callable[[float], str], Callable[[float], str] | None]` — `(feature_key, label, formatter, interpreter|None)`.

### Formatters
- `_pct_signed(v: float) -> str` — multiply×100, signed % (for fractional values).
- `_pct_unscaled_signed(v: float) -> str` — signed %, no scaling (for values already %).
- `_plain(v: float) -> str` — one-decimal float, no unit.
- `_ratio(v: float) -> str` — "1.1x" form.
- `_dollars_m(v: float) -> str` — "-$72.0M" form.

### Interpreters
- `_rsi_band(v) -> str` — `"(overbought)"` >70, `"(oversold)"` <30, else "".
- `_position_band(v) -> str` — `"(at high)"` / `"(at low)"` within 1% of extreme.
- `_cluster_sell_band(v) -> str` — `"(cluster sell)"` if flag set.
- `_cluster_buy_band(v) -> str` — `"(cluster buy)"` if flag set.
- `_golden_cross_band(v) -> str` — `"(golden cross)"` if flag set.
- `_death_cross_band(v) -> str` — `"(death cross)"` if flag set.
- `_planned_sale_band(v) -> str` — `"(all 10b5-1 — neutral)"` ≥0.9, `"(mostly 10b5-1 — neutral)"` ≥0.7.

### Registries (`list[_BulletEntry]`)
- `TECHNICAL_BULLETS` — RSI, momentum, relative-strength vs SPY/sector, 52w distance, MA crossover flags, vol_ratio, atr%, beta_confidence_damping.
- `FUNDAMENTAL_BULLETS` — PE/PEG, growth, margins, RoE, filings, insider flow + cluster flags + planned-sale ratio.
- `NEWS_BULLETS` — count_7d, pct_pos/neg_7d, polarity, social_volume_z.
- `SMART_MONEY_BULLETS` — politician/13F flow.
- `SOCIAL_BULLETS` — mentions, aggregate score, velocity, platform disagreement.
- `_ANALYST_BULLETS: dict[str, list[_BulletEntry]]` — name → registry.
- `_TAG_LINE_LABEL: dict[str, str]` — name → "Rationale tags" / "Closed-vocab tags".
- `_ANALYST_ORDER: tuple` — display order (technical, fundamental, news, smart_money, social).

### Internal helpers
- `_render_features(features, bullets) -> list[str]` — bullet-line renderer; key-absent skips; value-`None` emits "(no data)".
- `_render_report(report) -> list[str]` — emits "-> Report summary:" + "-> Drivers:" block.
- `_render_analyst(name, ev | None) -> str` — one analyst slot (missing / no-data / full).
- `_analyst_display_name(name) -> str` — "smart_money" → "SmartMoney".

### Public
- `render_ticker_block(te: TickerEvidence) -> str` — full per-ticker prompt block.
- `render_all_ticker_blocks(items: list[TickerEvidence]) -> str` — concatenates with divider; `"(no evidence this tick)"` on empty.

---

## src/contract/extractors/_sector_map.py
- `SECTOR_TO_ETF: dict[str, str]` — Finnhub sector strings → SPDR ETF ticker.

## src/contract/extractors/__init__.py
- (empty file — package marker)

## src/contract/extractors/technical.py

### Constants
- `_KEYS: tuple[str, ...]` — locked feature catalogue: `rsi_14, pct_change_5d, pct_change_20d, vol_ratio_20d, atr_pct_14, dist_from_high_52w_pct, dist_from_low_52w_pct, golden_cross, death_cross, beta_confidence_damping, last_close`.

### Functions
- `_pct_change(prices, window) -> float | None`
- `_relative_strength(own_bars, ref_ph, *, window, as_of=None) -> float | None`
- `_bar_date(bar) -> date`
- `_zero_features() -> dict[str, float]`
- `_df_from_history(history) -> pd.DataFrame | None`
- `_emit_ratios_features(raw) -> dict[str, float]`
- `_resolve_bars(raw) -> list`
- `extract_technical_features(raw, ticker="", *, as_of=None, state=None) -> dict[str, float]` — public.
- `derive_technical_verdict(features, h: TechnicalHeuristics) -> AnalystVerdict` — public; synthesises an `AnalystReport` (F-contract-001).

## src/contract/extractors/fundamental.py

### Constants
- `_KEYS: tuple[str, ...]` — fundamentals feature catalogue (pe_*, growth, margins, filings counts, insider aggregates incl. cluster flags, planned-sale ratio, derivative counts).

### Helpers
- `_zero_features() -> dict[str, float]`
- `_f(value) -> float`
- `_parse_dt(raw_filed) -> datetime | None`
- `_extract_stats_features(stats)` / `_extract_filings_features(filings, now)` / `_item_counters_30d(filings, as_of)` / `_insider_per_code_aggregates(trades)` / `_insider_aggregates_from_flat(trades, as_of)` / `_derivative_aggregates(derivs, last_price, as_of)` / `_extract_insider_features_legacy(insider_sub, now)` (F-contract-011 dedupe).

### Public
- `extract_fundamental_features(raw, ticker="", *, as_of=None, state=None) -> dict[str, float]` — public.

## src/contract/extractors/news.py

### Constants
- `HALF_LIFE_HOURS: float = 24.0`
- `_KEYS` — includes `headline_polarity_mean` and `headline_polarity_mean_7d` (alias — F-contract-003) and `hours_since_latest_news` (9999.0 = no-data sentinel).

### Functions
- `_zero_features() -> dict[str, float]`
- `_parse_published_at(raw_value) -> datetime | None`
- `extract_news_features(raw, ticker="", *, as_of=None, state=None) -> dict[str, float]` — public.

## src/contract/extractors/social.py

### Constants
- `_KEYS` — `mention_count_*`, `social_aggregate_score` + `aggregate_score` back-compat alias, `score_velocity_24h`, `platform_score_disagreement`, `is_no_data`.

### Functions
- `extract_social_features(raw, ticker="", *, as_of=None, state=None, **_unused) -> dict[str, float]` — public.
- `derive_social_verdict(features, h: SocialHeuristics) -> AnalystVerdict` — public; synthesises AnalystReport.

## src/contract/extractors/smart_money.py

### Constants
- `_HOLDER_WINDOW_DAYS = 90`
- `_KEYS` — politicians (n_*, totals, net_flow_dollar), notable-holder aggregates (13d/13g, max_percent_of_class_30d, total_shares_held_30d), `is_no_data` (defaults to 1.0 — opposite of social).

### Functions
- `_zero_features() -> dict[str, float]`
- `_amount(filing) -> float`
- `_parse_dt(raw_filed) -> datetime | None`
- `_notable_holder_aggregates(filings, as_of)` (line 134)
- `_resolve_as_of(state) -> date`
- `extract_smart_money_features(raw, ticker="", *, as_of=None, state=None) -> dict[str, float]` — public.
- `derive_smart_money_verdict(features, h: SmartMoneyHeuristics) -> AnalystVerdict` — public; synthesises AnalystReport.

---

## src/agents/contract/__init__.py
- (single-line package marker; effectively empty)

## src/agents/contract/evidence_writer.py

### Constants
- `_EVIDENCE_KEYS: tuple[tuple[str, str], ...]` — pairs of `(state_key, analyst_label)`: `technical_evidence/technical`, `fundamental_evidence/fundamental`, `news_evidence/news`, `smart_money_evidence/smart_money`, `social_evidence/social`.

### class `EvidenceWriter(BaseAgent)`
- `name: str = "EvidenceWriter"`
- `db_session: Any = None` — SQLAlchemy session; None ⇒ no-op.
- `model_config = {"arbitrary_types_allowed": True}` — non-Pydantic field type.
- `async _run_async_impl(ctx)` — drains evidence dicts; calls `save_analyst_evidence` and `save_ticker_evidence`; commits.

### Factory
- `build_evidence_writer(db_session=None) -> EvidenceWriter`.

---

## Cross-module dedupe pointers (out-of-scope models that name-collide or carry equivalent prose)

Listed for the cross-module pass — these are NOT in my scope but the contract layer's vocabulary depends on them:

- `TickerStance` — `src/agents/strategist/stance_schema.py:65` — uses `extra="forbid"` per intent §3.2. Single `rationale` field.
- `StrategistDecision` / `StrategistLLMDecision` — `src/agents/strategist/schema.py:32,72` — same two-shape pattern as `TickerVerdict` / `LlmTickerVerdict` (F-contract-006).
- `PositionThesis` — `src/agents/strategist/position_thesis.py:54` — carries `last_reviewed_reason` audit field per §3.1.
- `Order` / `Execution` / `ClampRecord` — `src/orchestrator/state.py` — only one definition each (no duplication across `src/contract/` and elsewhere).
- `Portfolio` — `src/broker/portfolio.py:20`; `Fill` — `src/broker/protocol.py:11`.
- `TickerStanceRow` / `PortfolioSnapshotRow` — `src/orchestrator/persistence.py` (ORM rows, distinct from Pydantic contract).
- `PositionThesisCaps` — `src/config/strategist.py:103`.
- `Form4Bundle`, `InsiderTrade`, `NotableHolder`, `PoliticianTrade` — `src/data/models/trades.py`.
