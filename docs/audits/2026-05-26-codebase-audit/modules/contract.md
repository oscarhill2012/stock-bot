# Contract module — audit findings

Scope: `src/contract/` (recursive) + `src/agents/contract/` (recursive)
plus `tests/contract/` and `tests/unit/contract/`.

## F-contract-001
- **Category:** policy-mismatch
- **Severity:** P0
- **Location:** `src/contract/extractors/social.py:275-329`, `src/contract/extractors/technical.py:670-705`, `src/contract/extractors/smart_money.py:471-512`
- **Evidence:**
  ```
  # social.py:275-279
  # --- Synthetic AnalystReport -----------------------------------------------
  # Structured analysts have no LLM prose, but the schema requires ``report``
  # whenever ``is_no_data=False``.  Build a minimal report from the
  # deterministic signals so the uniform contract holds.
  ...
  return AnalystVerdict(..., report=report)
  ```
- **Intent violated:** intent.md §2.1 ("LLM analysts populate `AnalystReport`; deterministic analysts leave `report=None`") and §2.6 ("Deterministic analysts (Technical, SmartMoney, Social) leave ``AnalystVerdict.report`` as ``None`` — their cognition is fully captured by the verdict and extractor features; they have no prose to summarise.").
- **Suggested action:** investigate — either the intent doc is stale (the `_report_required_when_data_present` validator forces every non-no-data verdict to carry a report, so deterministic extractors now synthesise one) or the schema validator should exempt deterministic analysts. The current behaviour silently fabricates "synthetic LLM prose" for the strategist's per-ticker prompt, which contradicts both intent docs and the strategist-prompt `_render_report` branch that the renderer reads.
- **Notes:** This is the canonical Phase-11-missed contract drift: the new D1.1 validator (`evidence.py:136-155`) forced deterministic extractors to manufacture an `AnalystReport` to satisfy parsing, but neither the policy doc nor the extractor architecture acknowledges that the "LLM-only" report block is now populated by deterministic code. Either fix the schema to allow `report=None` when `analyst in {"technical", "social", "smart_money"}`, or update intent + extractor docstrings + downstream renderer expectations. P0 because the strategist prompt now contains hard-coded synthetic prose ("Technical analysis leans bullish: …") that the strategist may treat as analyst reasoning.

## F-contract-002
- **Category:** dead-code
- **Severity:** P1
- **Location:** `src/contract/evidence.py:281-305` (`AnalystEvidence.raw_text` field)
- **Evidence:** grep `raw_text` across `src/` returns only the field declaration; no producer in any analyst joiner or extractor, no consumer in strategist context_shim, evidence_view, or prompt renderer.
  ```
  src/contract/evidence.py:305:    raw_text: str | None = Field(default=None, max_length=10_000)
  tests/unit/agents/strategist/test_evidence_view_missing_report.py:48:        raw_text    = None,
  tests/unit/contract/test_evidence_raw_text.py:* (schema-only tests)
  ```
- **Intent violated:** n/a (intent doesn't mention it).
- **Suggested action:** delete the field and `tests/unit/contract/test_evidence_raw_text.py`. If a future spec wants raw provider text in the strategist prompt, reintroduce with at least one producer wired.
- **Notes:** Docstring claims "optional pass-through of the raw provider text the LLM analyst saw"; no LLM analyst writes it.

## F-contract-003
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** `src/contract/extractors/news.py:29-30`
- **Evidence:**
  ```
  29:    "headline_polarity_mean",        # renamed from headline_polarity_mean_7d
  30:    "headline_polarity_mean_7d",     # back-compat alias — same value
  ```
  Only `headline_polarity_mean_7d` is read (by `strategist_prompt.py:377`). The non-suffixed key has no production consumer (grep returns only the extractor itself and the test that pins both aliases).
- **Intent violated:** n/a.
- **Suggested action:** delete the non-suffixed alias once a sweep confirms no external persistence/cache reads it. Tests `tests/unit/contract/extractors/test_news.py:51-57,69,100` would simplify.
- **Notes:** Live silent duplication of the same float in every news evidence row.

## F-contract-004
- **Category:** dedupe-candidate
- **Severity:** P1
- **Location:** `src/contract/evidence.py:109-156` (`AnalystVerdict.rationale`) vs `report.summary`
- **Evidence:** Docstring at `evidence.py:116-126` states LLM analysts "no longer emit it — `report.summary` carries the same surface and the duplication was driving the constrained-decoder repetition pathology." But deterministic extractors continue to populate `rationale` as `, ".join(factors)[:160]` and ALSO emit a synthetic `report.summary` (`social.py:315-319`, `technical.py:691-693`, `smart_money.py:496-500`) — both fields hold semantically equivalent prose for deterministic verdicts.
- **Intent violated:** §3.1 "rationale" glossary entry implies one canonical prose field; §3.2 cluster 1 flags prose-field proliferation.
- **Suggested action:** consolidate. Either drop `rationale` for deterministic verdicts (let `report.summary` carry the same prose; renderer reads from one place), or drop the synthetic `report` block (see F-contract-001). Two storage sites for the same string in every deterministic evidence row.
- **Notes:** Compounds with F-contract-001. Together they answer intent §3.2 cluster 1 (`rationale` / `report.summary` / others) for the contract layer: yes, on every deterministic verdict, two fields hold the same prose.

## F-contract-005
- **Category:** silent-failure
- **Severity:** P1
- **Location:** `src/contract/digest.py:69-90` (`_fill_missing`)
- **Evidence:**
  ```
  82-89:        filled[name] = AnalystEvidence(...,
                    verdict=AnalystVerdict(lean="neutral", magnitude=0.0,
                                           confidence=0.0, rationale="(no analyst output this tick)",
                                           key_factors=[], is_no_data=True))
  ```
  The fill path produces an `AnalystEvidence` with `verdict.is_no_data=True` and `report=None`. The schema validator at `evidence.py:136-155` only raises when `is_no_data=False and report=None` — so the fill is legal — but it means a missing analyst is silently coerced to "no data" with no log/warn. Combined with the synthetic-report extractors (F-contract-001), a downstream observer can no longer distinguish "extractor wrote a real verdict with synthetic report" from "extractor failed to produce evidence and digest neutral-filled".
- **Intent violated:** test-policy §A.7 and §G.7 — `is_no_data=True` fallbacks are silent-failure attractors.
- **Suggested action:** investigate — at minimum, surface a structured-log warning when `_fill_missing` neutralises a slot. Possibly raise on missing analysts named in `weights` if the strategist contract guarantees all five analysts always run (intent §7.1 says smart_money "runs every tick").
- **Notes:** Per intent §7.1, every analyst registered in the pool runs every tick — so any missing analyst in `weights` is a pipeline bug, not a benign sparse case. Silent neutral-fill hides that bug.

## F-contract-006
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/contract/evidence.py:158-171` (`TickerVerdict`) vs `src/contract/evidence.py:174-266` (`LlmTickerVerdict`)
- **Evidence:** `LlmTickerVerdict` is the narrow LLM emit; `TickerVerdict` is `AnalystVerdict + ticker`. The joiner round-trips one to the other via `model_dump → model_validate` (`tests/contract/test_llm_ticker_verdict.py:211-218`). `TickerVerdict` inherits `AnalystVerdict`'s `_report_required_when_data_present` validator, so the downstream class can no longer represent the "no data, no report" case differently from `LlmTickerVerdict` (which forbids `report=None` entirely). Two-class split is intentional per docstring, but the inflation gap (`rationale=""` default; `is_no_data` default `False`) is the exact silent-failure attractor §G.7 warns about.
- **Intent violated:** §3.2 cluster 5 (`StrategistLLMDecision` vs `StrategistDecision` — same two-shape pattern).
- **Suggested action:** investigate — pattern is well-documented in the `LlmTickerVerdict` docstring; flag only because the two-shape split is exactly the bug class Phase-11 missed in the strategist layer (`rationale`/`reason`/`catalyst`).
- **Notes:** No outright dedupe action — the split is load-bearing for Vertex's constrained-decoder behaviour — but worth a confirmation glance from human triage that the joiner is the only `LlmTickerVerdict → TickerVerdict` site.

## F-contract-007
- **Category:** dead-test
- **Severity:** P1
- **Location:** `tests/unit/contract/test_invariants_doc_carveout.py:14-34`
- **Evidence:** Test asserts presence of `Phase8-contract-audit-fixes/contract-audit.md` and asserts the in-tick carve-out clause is in `docs/contract-invariants.md`. Per intent §7.2, `_strategist_validation_callback` is dead in production — the carve-out doc is scheduled for removal. The Phase8 doc was already deleted (per the conversation git status showing `docs/Phase11-*` deletions and the project's "audit, don't restart" preference).
- **Intent violated:** §7.2 — the carve-out documentation is queued for removal.
- **Suggested action:** delete — this test pins doc text that intent has already decided to retire. Will cause spurious failure on the doc-fix.
- **Notes:** Also references `docs/Phase8-contract-audit-fixes/` which I am forbidden to read; the test asserts that file exists.

## F-contract-008
- **Category:** test-gap
- **Severity:** P2
- **Location:** `tests/unit/contract/test_evidence.py:88-114` (`test_evidence_valid`, `test_evidence_feature_warnings_default_empty`)
- **Evidence:** Tests construct an `AnalystEvidence` and assert only field-presence (`e.ticker == "AAPL"`, `e.feature_warnings == []`). No assertion that the verdict round-trips, no assertion on `raw_text`, no positive content check on `features` aside from a single key.
- **Intent violated:** test-policy §A.7 ("positive output state"), §E ("asserting only on counts").
- **Suggested action:** investigate — schema tests are necessarily thin, but pair every "field accessible" assertion with at least one content check.
- **Notes:** Low-cost.

## F-contract-009
- **Category:** test-gap
- **Severity:** P1
- **Location:** `tests/unit/contract/extractors/test_*.py` — only `test_extractor_as_of.py` exercises happy paths positively; the per-extractor unit tests under `tests/unit/contract/extractors/` cover feature math but few of them assert what happens when raw payloads are malformed (e.g., missing required keys, wrong dtypes, naive timestamps).
- **Evidence:** `extract_fundamental_features` (`fundamental.py:579-691`) silently defaults to `_zero_features()` on `if not raw: return out` and on every parse failure inside `_f`. `extract_news_features` similarly returns zero-features on any parse error. No test asserts the extractor surfaces these as `feature_warnings` (the field exists for this exact purpose, `evidence.py:289-292`).
- **Intent violated:** test-policy §A.7, §E.
- **Suggested action:** investigate; test-policy §A.7 mandates "deliberately exercise the everything-went-wrong branches".
- **Notes:** `feature_warnings` is declared on `AnalystEvidence` but never populated by any extractor in `src/contract/extractors/` (grep `feature_warnings` returns only the schema declaration and digest neutral-fill `feature_warnings=[]`). Possible companion P1 dead-code finding — see F-contract-010.

## F-contract-010
- **Category:** dead-code
- **Severity:** P2
- **Location:** `src/contract/evidence.py:303` (`feature_warnings: list[str] = Field(default_factory=list)`)
- **Evidence:** No producer in `src/contract/extractors/` populates `feature_warnings`. Grep:
  ```
  src/contract/evidence.py:303:    feature_warnings: list[str] = Field(default_factory=list)
  src/contract/digest.py:81:                features={}, feature_warnings=[],
  src/agents/contract/evidence_writer.py:101:    feature_warnings=ev_dict.get("feature_warnings", []),
  ```
  And reverse: `grep -rn "feature_warnings" src/agents/analysts/` returns nothing. The persistence writer reads the field, but no analyst ever sets it.
- **Intent violated:** n/a.
- **Suggested action:** investigate — either wire warning emission into extractors (the field's documented purpose: "downstream consumers can tell '0.0 because missing' from 'real 0.0'") or delete the field plus the persistence column.

## F-contract-011
- **Category:** dedupe-candidate
- **Severity:** P2
- **Location:** `src/contract/extractors/fundamental.py:481-577` (`_extract_insider_features_legacy`) vs `:344-405` (`_insider_aggregates_from_flat`)
- **Evidence:** Two code paths for insider feature extraction — flat-list (Phase 7) and `Form4Bundle` (legacy Phase 5). Selector at `:672` is `if "insider_trades" in raw`. Production analyst (`src/agents/analysts/fundamental/joiner.py`) consistently passes the flat-list shape; only legacy tests exercise the `Form4Bundle` branch.
- **Intent violated:** n/a.
- **Suggested action:** investigate whether any live provider still produces `Form4Bundle` payloads; if not, retire the legacy path.

## F-contract-012
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/contract/digest_defaults.py`
- **Evidence:** 19-line module exporting a single dict and a single float constant. Imported by `agents/strategist/context_shim.py` and tests only. Could collapse into `contract/digest.py` to reduce the surface.
- **Intent violated:** n/a.
- **Suggested action:** investigate — the file's own docstring says "if a future spec needs these tunable without code changes, promote to `config/digest.json` + a loader." Either inline now or promote when the time comes; the standalone file adds no value today.

## F-contract-013
- **Category:** policy-mismatch
- **Severity:** P2
- **Location:** `src/contract/ticker_evidence.py:50-63` (`TickerEvidence.last_price`)
- **Evidence:**
  ```
  Downstream renderers must treat ``None`` and ``0.0`` as
  "no price" — both can arise depending on the path that populates it.
  ```
  Renderer `strategist_prompt.py:659` does honour both (`if te.last_price is not None and te.last_price > 0`). However the schema permits `last_price=0.0` as a valid "real" price; the convention is documented only in the field docstring. Two sentinels (`None` and `0.0`) for one state is fragile.
- **Intent violated:** silent-failure attractor pattern.
- **Suggested action:** investigate — pick one sentinel (`None`), enforce at schema (`PositiveFloat | None`), update all writers to never emit `0.0` for "no price".
- **Notes:** Closely related to the `is_no_data=True` attractor §G.7 — same shape of silent ambiguity.

## F-contract-014
- **Category:** over-abstraction
- **Severity:** P3
- **Location:** `src/contract/strategist_prompt.py:679-702` (`render_all_ticker_blocks`)
- **Evidence:** Used only by `agents/strategist/context_shim.py:49`. The divider/empty-list logic is a single call site.
- **Intent violated:** n/a.
- **Suggested action:** none required; flag for human triage if the strategist surface ever consolidates.

---

## Test inventory

| File | tests | role | finding |
|---|---|---|---|
| `tests/contract/test_evidence_schema.py` | 3 | D1.1 validator | OK |
| `tests/contract/test_llm_ticker_verdict.py` | ~10 | narrow-emit contract | OK, exemplary positive assertions |
| `tests/contract/test_no_hardcoded_models.py` | 1 | AST sweep | OK |
| `tests/contract/test_provider_shapes.py` | n/a | live-gated | out of scope |
| `tests/unit/contract/test_evidence.py` | 8 | schema | F-contract-008 (thin) |
| `tests/unit/contract/test_evidence_raw_text.py` | 1 | dead field | F-contract-002 dead |
| `tests/unit/contract/test_analyst_report.py` | 7 | schema | OK |
| `tests/unit/contract/test_ticker_evidence.py` | ~9 | schema | OK |
| `tests/unit/contract/test_digest.py` | 13 | aggregator | OK, positive assertions |
| `tests/unit/contract/test_digest_defaults.py` | 3 | constants | OK |
| `tests/unit/contract/test_extractor_as_of.py` | 3 | signature contract | OK |
| `tests/unit/contract/test_invariants_doc_carveout.py` | 2 | doc presence | F-contract-007 dead |
| `tests/unit/contract/test_strategist_prompt_layout.py` | ~30 | renderer | OK, heavy positive |
| `tests/unit/contract/extractors/test_*.py` | 58 | extractor math | F-contract-009 (no failure-branch coverage) |
| `tests/unit/test_analyst_name_literal.py` | 3 | Literal membership | OK |
