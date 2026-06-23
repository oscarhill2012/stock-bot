# iter-11 fix decisions (signed off 2026-06-23)

Sign-off record for the three audit-driven specs in this directory. The specs
themselves retain the full analysis; this file records which option was chosen
and why, so the chosen path is unambiguous at implementation time.

## 1. Technical relative-strength lean — PARKED

Spec: [technical-relative-strength-lean.md](technical-relative-strength-lean.md)

**Decision: do not implement now.** The empirical follow-up showed RS-vs-SPY is
not directionally predictive over our windows (contrarian, sign-flips across
window/horizon; the biggest laggards posted the *highest* forward excess).
"Lean on RS" (option b) would likely degrade the iran window; the safe
"confidence-only" variant (option c) is verdict-neutral by default and so does
almost nothing until the strategist's confidence-weighting is itself validated.
RS-vs-sector is also 0% populated until the company_ratios refetch lands.

**Revisit when:** the ≥6-month multi-regime window exists AND sector data is
populated, so the feature can actually be validated rather than fitted to two
windows.

## 2. News bearish over-confidence — IMPLEMENT (downstream cap)

Spec: [news-bearish-overconfidence.md](news-bearish-overconfidence.md)

**Decision: deterministic, observable priced-in correction in a NEW stage after
the `AnalystPool`**, not inside `NewsJoinerAgent` and not via pipeline
re-ordering. Rationale: the news analyst is deliberately sentiment-only and
cannot see price; after the pool, technical's `pct_change_20d` momentum is
already in `state`, so a post-pool stage can read it without re-ordering a
parallel stage or double-fetching prices. News stays sentiment-only.

**Trigger discipline (from the regime evidence):** the correction must only
fire on bearish verdicts where recent momentum is *strongly positive* (the
priced-in setup that dominates the baseline −531 bps cell). It must NOT fire in
the falling/dispersing regime where bearish-news calls were mostly correct
(iran +20d bearish ≈ +47 bps). Threshold is config-gated.

**RESOLVED — lean vs confidence (signed off 2026-06-23): downgrade the lean to
*neutral*** on the priced-in trigger, not merely trim confidence. Rationale: the
scoreboard scores `lean`, not confidence — a confidence-only cap would remove the
losing *trades* (via strategist weighting) but would be invisible in the bearish
scoreboard cell, and the honest reading of strongly-priced-in bad news is
"neutral", not "weak bearish". The neutral downgrade both removes the losing
signal and is measurable. The implementing agent must downgrade `lean` →
`neutral` (carrying the original verdict into the trace per the observability
note below), not adjust `confidence` alone.

**Observability (silent-failure rule):** every time the correction fires it must
record the original verdict (trace), annotate a `priced_in_momentum` key_factors
tag, and increment a `metrics.md` counter mirroring the existing
`hallucinated_stances` precedent.

## 3. key_factors vocabulary enforcement — IMPLEMENT (recommended adopt/drop)

Spec: [key-factors-vocabulary-enforcement.md](key-factors-vocabulary-enforcement.md)

**Decision: normalise → drop → count, never raise.** Per-tag pipeline at the
joiner choke point: pass valid tags; deterministic namespace coercion (recovers
~99% of news, ~half of fundamental); small config alias map; drop the
genuine-garbage tail. Every coercion and drop is logged + counted in
`metrics.md`. Not a schema `Enum`/`Literal` (would bloat the Vertex output
schema and risk constrained-decoder pathologies).

**Vocabulary adopt/drop:**
- ADOPT — new `driver:` namespace: `revenue_growth`, `profit_margin`,
  `free_cash_flow`, `roe` (≈85% of invented fundamental tags; the prompt's own
  ratio reasoning currently has nowhere to land).
- ADOPT — `insider_signals` +=: `discretionary_buy`, `conviction_buy`,
  `conviction_sell`, `neutral`.
- ADOPT — `risks` +=: `export_control`, `acquisition`.
- DROP (count, don't adopt): `AI_integration`, `financial_strength`,
  `operational_efficiency`, `capital_allocation`, `investment`, `cloud_growth`,
  `shareholder_returns`.
- SEPARATE: decide whether to canonicalise the news prompt's `material:<bool>`
  as `material:{true,false}` or cut it — to settle during implementation.

Also fix the stale `config/README.md` line that falsely claims a closed-vocab
check already exists.
