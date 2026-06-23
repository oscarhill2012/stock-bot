# Spec — closed-vocabulary enforcement on `key_factors`

**Status:** draft, awaiting sign-off (see §6 Open questions).
**Origin:** Phase 13 iter-11 LLM-analysts audit, finding **F-2** —
`docs/Phase13-analyst-improvement/audit-iter-11/llm-analysts-audit.md`.
**Scope:** the `key_factors` tag channel emitted by the two LLM analysts
(News, Fundamental). Deterministic analysts (Technical, Social, SmartMoney)
build `key_factors` from their own code-side tag lists and are out of scope
(they cannot emit off-vocab tags — see §3.4).

British English throughout. This is an implementation-ready spec, **not**
production code. No `src/` or `config/` file is modified by this document.

---

## 1. Problem statement (with evidence)

### 1.1 What the prompts mandate

Both LLM analysts are instructed to emit `key_factors` using a **closed,
namespaced vocabulary** baked into the prompt from
`config/analyst_heuristics.json`:

- **Fundamental** (`src/agents/analysts/fundamental/prompts.py` line 84):
  `guidance:<value>`, `tone:<value>`, `risk:<value>` (with an optional
  `_added | _removed | _intensified` suffix), `insider:<value>`,
  `going_concern:true`.
- **News** (`src/agents/analysts/news/prompts.py` line 46):
  `catalyst:<type>`, `novelty:<level>`, `direction:<value>`,
  `material:<bool>`.

The canonical value lists are the `fundamental_vocabulary`
(`guidance`/`tone`/`risks`/`insider_signals`) and `news_vocabulary`
(`catalysts`/`novelty`/`direction`) blocks of
`config/analyst_heuristics.json`, typed by `FundamentalVocabulary` /
`NewsVocabulary` in `src/agents/analysts/heuristics.py`.

### 1.2 What actually validates the emitted tags: **nothing**

`LlmTickerVerdict.key_factors` and `AnalystVerdict.key_factors`
(`src/contract/evidence.py` lines 329 and 128) are both typed
`list[str] = Field(default_factory=list, max_length=8)`. The **only**
constraint is the length cap of 8. There is:

- no `Enum` / `Literal` element type,
- no Pydantic field/model validator that inspects tag *content*,
- no normalisation pass in either joiner (`fundamental/joiner.py`,
  `news/joiner.py`) — they call `LlmTickerVerdict.model_validate(...)` then
  `to_ticker_verdict()`, neither of which looks at tag content.

So an off-vocabulary tag passes **silently** all the way through. (The
config README even claims, at `config/README.md` line 181, "Any tag not in
the list will fail the extractor's closed-vocab check" — that check does not
exist. The doc describes a guard that was never built. This is itself a
silent-failure: the documented contract and the code disagree, and nothing
flags it.)

### 1.3 Measured distribution (iter-11 artefacts)

Re-derived independently for this spec by parsing the canonical
`02_fundamental_verdict` / `02_news_verdict` blocks of all 60 traces per
window (stdlib-only parser), validating each tag against the namespaced
vocabulary expanded from `config/analyst_heuristics.json` (risk suffixes
and `material:{true,false}` included). The figures reproduce the audit's
F-2 headline (29 % / 30 % off-vocab) and refine it by **failure mode**:

| window | analyst | off-vocab | namespace-mismatch | invented |
|---|---|---:|---:|---:|
| baseline-2025-09 | fundamental | 1738 / 5966 (29.1 %) | 856 | 882 |
| baseline-2025-09 | news | 1760 / 6501 (27.1 %) | **1734 (98.5 %)** | 26 |
| iran-conflict-2026-02 | fundamental | 1786 / 6046 (29.5 %) | 828 | 958 |
| iran-conflict-2026-02 | news | 2009 / 6619 (30.4 %) | **1993 (99.2 %)** | 16 |

- **"namespace-mismatch"** = the model dropped the required prefix but the
  bare value is a real vocab token (`macro` → `risk:macro`,
  `product_launch` → `catalyst:product_launch`).
- **"invented"** = neither a valid namespaced tag nor a recognised bare
  value.

**The two analysts have completely different pathologies, and this drives the
fix:**

- **News is almost pure prefix-drop.** ~99 % of its off-vocab tags are valid
  vocabulary values with the namespace stripped. Top offenders:
  `product_launch` (380/461×), `regulatory`, `guidance`, `macro`, `legal`,
  `upgrade`, `none`, `earnings`, `downgrade`, `m_and_a` — every one of these
  is a real `catalysts` value missing its `catalyst:` prefix. Genuinely
  invented news tags are a long tail of ~20 one-offs (`partnership`, `AI`,
  `analyst_sentiment`, `material:bool`). **News is a clean coercion target.**

- **Fundamental is ~50/50, and its "invented" half is structured, not
  random.** Pooling both windows, the 1840 invented occurrences are
  dominated by what look like **ratio field-names leaking into the tag
  stream** (the COMPANY RATIOS block field labels):

  | invented tag | pooled count |
  |---|---:|
  | `revenue_growth` | 508 |
  | `profit_margin` | 480 |
  | `free_cash_flow` | 294 |
  | `revenue_growth_YoY` | 144 |
  | `ROE` | 94 |
  | `revenue_growth_yoy` | 36 |
  | `operational_efficiency` | 32 |
  | `financial_strength` | 32 |
  | `insider:discretionary_buy` | 28 |
  | `competition_intensified` | 20 |
  | `AI_integration` | 20 |
  | `insider:conviction_sell` | 16 |

  The top four (`revenue_growth`, `profit_margin`, `free_cash_flow`, `ROE`
  + casing/suffix variants) account for **~85 % of all invented fundamental
  occurrences**. There is no namespace these belong to — the vocabulary
  simply has **no slot for "the valuation/fundamentals driver"** even though
  the prompt spends its longest section (§1 "Anchor on EXPECTATIONS first")
  telling the model to reason from exactly these ratios. The model is
  inventing the tags the prompt's own reasoning demands.

  A second cluster reveals **genuine vocab gaps in `insider_signals`**:
  `insider:discretionary_buy` (28×), `insider:conviction_sell` (16×),
  `insider:neutral` (14×). The prompt's hard rules R-conviction explicitly
  discuss conviction *buys* and conviction *sells* (lines 268–272), but
  `insider_signals` has `cluster_buying` / `discretionary_sale_dominant`
  and **no** `discretionary_buy`, `conviction_buy`, or `conviction_sell`.
  The model is correctly tagging a signal the vocab forgot to name.

  A third cluster is **missing risk values** surfacing in correct namespaced
  form: `risk:export_control` (8×), `risk:acquisition_*` (~28× across
  variants). These are valid `risk:`-prefixed tags whose *value* isn't in
  `risks`.

  The genuine garbage residue (`AI_integration`, `financial_strength`,
  `operational_efficiency`, `capital_allocation`, `investment`,
  `cloud_growth`, `shareholder_returns`) is small — a few dozen each.

**Conclusion that drives the design:** off-vocab is *not* one phenomenon.
News needs **coercion** (re-prefix). Fundamental needs **coercion + a vocab
extension** (the ratio-driver and insider-conviction slots the prompt
already reasons about) **+ a drop/reject net** for the genuine-garbage tail.
A single "reject the verdict" rule would throw away ~30 % of otherwise-good
verdicts; a single "drop the tag" rule would silently bin a real, namable
signal. The right fix is layered.

### 1.4 What happens to an off-vocab tag at each consumer today

| consumer | file | behaviour with an off-vocab tag |
|---|---|---|
| LLM emit-schema validation | `LlmTickerVerdict` (`evidence.py:329`) | accepted — only length is checked |
| joiner inflation | `{news,fundamental}/joiner.py` → `to_ticker_verdict()` | passed through unchanged |
| canonical verdict | `AnalystVerdict.key_factors` (`evidence.py:128`) | accepted |
| evidence record | `AnalystEvidence` → `model_dump(mode="json")` | serialised verbatim |
| SQLite persistence | `persistence.py:258` `key_factors_json=json.dumps(...)` | stored verbatim in the KB substrate |
| strategist prompt | `strategist_prompt.py:642` | rendered verbatim into the `-> Tags:` line the strategist reads |
| decision logger / traces | `02_*_verdict` trace blocks | logged verbatim |

So an off-vocab tag corrupts **both** machine-aggregatable channels the
analysts produce: the persisted KB rows (the future learning loop's only
structured tag key — see `evidence.py` lines 9–14) and the live strategist
context. The audit's framing is correct: this is the one
machine-aggregatable channel, and 30 % of it is noise.

---

## 2. The behaviour decision (core of the spec)

When an off-vocab tag arrives at the validation boundary, the candidate
policies are:

- **(a) RAISE** — reject the whole verdict. Loudest. But the verdict's
  `lean`/`magnitude`/`confidence`/`report` are usually fine (audit F-3: the
  reasoning is genuinely data-grounded), and a rejected verdict falls back to
  the joiner's `is_no_data` synthetic — we'd lose ~30 % of *all* verdicts and
  the scoreboard `lean` signal with them, to fix a tag-hygiene problem. Net
  harm.
- **(b) DROP the offending tag**, keep the verdict, record a counter + log
  line. Observable, not silent. But for news (99 % prefix-drop) this throws
  away the entire tag — we'd lose almost all news tags despite the model
  having emitted a recoverable value.
- **(c) COERCE** via a defined alias/normalisation map (`macro` →
  `risk:macro`, `product_launch` → `catalyst:product_launch`), keep the
  verdict, log every coercion.

### 2.1 Recommendation: a layered **normalise → drop → count**, never raise

Given the §1.3 distribution and the project's silent-failures-are-the-bug-class
rule, the recommended default is an **ordered pipeline applied per tag at the
joiner boundary**, in this order:

1. **Already valid?** Pass through untouched.
2. **Suffix-aware namespace coercion (deterministic, no hand-maintained map).**
   If the tag is bare (no `:`), try prefixing it with each namespace legal for
   that analyst and accept the first that resolves to a valid vocab tag —
   honouring the risk-suffix scheme so `competition_intensified` →
   `risk:competition_intensified`. This single rule recovers ~99 % of news
   off-vocab and ~half of fundamental off-vocab **without** a per-token alias
   table to maintain. Each coercion is logged + counted.
3. **Explicit alias map (small, config-driven) for the known structured
   inventions** that aren't bare-value drops — casing/synonym fixes
   (`ROE`→`roe`-namespaced form, `revenue_growth_YoY`→`revenue_growth_yoy`)
   and `material:bool`/`material`→ the spec'd `material:` form. Logged +
   counted as coercions.
4. **Drop + count + log** anything still unresolved (the genuine-garbage
   tail). The verdict survives with its remaining valid tags.

**Never raise on tag content.** The verdict is otherwise valid and load-bearing
(the scoreboard scores `lean`, not tags); rejecting it would convert a hygiene
defect into a data-loss defect, which is the *worse* silent failure (the lean
signal vanishes into a synthetic no-data with no operator-visible reason tying
it to a tag). The loudness requirement is satisfied by **observability, not
exceptions**: every coercion and every drop emits a structured log event and
increments a per-run counter surfaced in `metrics.md` (§3.4), exactly mirroring
the existing `hallucinated_stances` precedent. This makes the bad data
*impossible to ignore* in the run report while not destroying the good data
riding alongside it — which is the correct reading of the house rule (the bug
class is *silent acceptance*, not *acceptance*).

### 2.2 …but coercion alone is the wrong answer for the structured inventions

The ratio-driver leak (`revenue_growth`, `profit_margin`, `free_cash_flow`,
`ROE`) and the insider-conviction leak (`discretionary_buy`,
`conviction_buy/sell`) are **not** model errors to be coerced or dropped —
they are **categories the vocabulary is missing**, emitted because the prompt's
own reasoning sections demand them. Coercing/dropping them would silently
discard a real, namable signal (a subtler silent failure: the data looks
clean but a whole class of driver has been deleted).

So the fix has a **second, mandatory limb: extend the vocabulary** (§3.3,
recommendations in §6) so these become first-class valid tags. After the
extension, the residual genuine-garbage tail (a few hundred occurrences of
`AI_integration` / `financial_strength` / etc.) is what the drop-net in step 4
legitimately catches.

This makes the policy honest: we coerce recoverable prefix-drops, **adopt** the
categories the model keeps correctly identifying, and drop-with-a-loud-counter
only the genuine noise.

---

## 3. Exact implementation surface

### 3.1 Where validation runs — the joiner, via a shared normaliser

Validation/normalisation runs **at parse time in both joiners**, in the
`else` branch right after `to_ticker_verdict()` and before the
`TickerVerdict` is appended (`fundamental/joiner.py` ~line 101,
`news/joiner.py` ~line 90). Rationale:

- It is the **single choke point** every LLM verdict already flows through
  (the `LlmTickerVerdict` → `TickerVerdict` inflation), and it already owns
  the loud-failure boundary (see `to_ticker_verdict`'s docstring).
- It has the analyst identity in hand (`"news"` / `"fundamental"`), which
  selects the right vocabulary and namespace set.
- It is where the obs/retry counters are already collected (lines 140–157),
  so the new counter has a natural home.

**Not** in the Pydantic schema as an `Enum`/`Literal` element type, because:

1. A `Literal[...]` element type would make `LlmTickerVerdict` the ADK
   `output_schema`, pushing the full closed vocabulary into the JSON Schema
   sent to Vertex. The schema docstring (`evidence.py` lines 280–298)
   documents that the constrained decoder mishandles rich schema constraints
   (the `max_length` pad-target pathology); an enum of ~40 string literals
   on a list element is exactly the kind of constraint that risks decoder
   pathologies and brittle retries. The deliberate design choice in this
   file is to keep the emit-schema *loose* and validate in Python.
2. A *raising* field validator on the schema would re-introduce policy (a)
   (reject the verdict), which §2 argues against.
3. The vocabulary lives in config and is meant to change without a code
   change (it is `extra="forbid"`-typed in `heuristics.py`); a `Literal`
   would hardcode it into the schema and break that convention.

The normaliser is a **pure function in a new module**
`src/agents/analysts/key_factors.py` (shared by both joiners), with a
signature shaped like:

```python
def normalise_key_factors(
    tags: list[str],
    *,
    analyst: Literal["news", "fundamental"],
) -> KeyFactorNormalisation:
    """Coerce LLM-emitted key_factors onto the closed vocabulary.

    Applies the ordered pipeline (pass-through → namespace coercion →
    alias map → drop) described in the vocabulary-enforcement spec §2.1.
    Pure and deterministic; reads the closed vocabulary from
    ``load_heuristics()`` and the alias map from the same config block.

    Parameters
    ----------
    tags:
        The raw ``key_factors`` list as emitted by the LLM.
    analyst:
        Which analyst emitted them — selects the vocabulary and the set
        of legal namespaces.

    Returns
    -------
    KeyFactorNormalisation
        ``.tags`` — the cleaned, valid, de-duplicated tag list (order
        preserved, length still capped at 8 by the schema downstream);
        ``.coerced`` — list of (original, replacement) pairs;
        ``.dropped`` — list of unresolved originals.
    """
```

The joiner then logs/counts from the returned record and feeds
`result.tags` into the `TickerVerdict`. The branch-failed synthetic path
(`raw_v is None`) emits `key_factors=[]` and is unaffected.

### 3.2 Observability — make every coercion and drop loud

Mirror the `hallucinated_stance` precedent exactly
(`src/agents/executor/_verb_dispatch.py` → `reporting.py` lines 1452–1456,
1497, 1529):

- **Structured log events** on a stable logger/message so the reporting
  aggregator can count them from `obs/logs/*.json`:
  - `message="key_factor_coerced"` (one per coercion), with `extra`
    carrying `analyst`, `ticker`, `original`, `replacement`.
  - `message="key_factor_dropped"` (one per dropped tag), `extra` carrying
    `analyst`, `ticker`, `original`.
- **Aggregate in `reporting.py`** (`_collect_obs_aggregates`, the loop at
  lines 1441–1462): add `key_factors_coerced` and `key_factors_dropped`
  integer counters next to `hallucinated_stances`, thread them into the
  returned dict (lines 1484–1499), and render them in
  `_format_obs_section` (line 1529-area) as their own metrics.md lines, e.g.
  `key_factors coerced: **N** | dropped: **M**`. A non-zero `dropped` is the
  operator's signal that the vocabulary may be missing a real category and
  should be reviewed (the §6 sign-off loop).
- Include both counters in the `nothing_found` suppression guard (line 1472)
  so an empty obs tree still suppresses the section.

This is the loudness the house rule demands: a run that silently mangled
1700+ tags today will, post-fix, print exactly how many it coerced and how
many it threw away, in the same report the operator already reads for
hallucinations.

### 3.3 Config additions

All in `config/analyst_heuristics.json`, with `config/README.md` updated in
the same change (config convention):

1. **Vocabulary extension** (the §2.2 / §6 limb — values pending sign-off):
   - `fundamental_vocabulary` — a **new namespace** for the
     fundamentals-driver leak. Recommended: add a `drivers` list
     (`revenue_growth`, `profit_margin`, `free_cash_flow`, `roe`) surfaced
     in the prompt as `driver:<value>`, since these are *driver* tags, not
     *risk* tags. Extend `insider_signals` with `discretionary_buy`,
     `conviction_buy`, `conviction_sell`, `neutral`. Extend `risks` with
     `export_control` and `acquisition` (covers `risk:export_control`,
     `risk:acquisition_*`).
   - This requires adding a `drivers: list[str]` field to
     `FundamentalVocabulary` in `heuristics.py` and a `driver:<value>` line
     to `fundamental/prompts.py`'s closed-vocabulary block. (The News vocab
     needs no *extension* — its inventions are negligible; it only needs the
     `material:<bool>` form clarified, see below.)
2. **Alias map** (step 3 of the pipeline) — a new config block keyed by
   analyst, e.g. `key_factor_aliases`, mapping the known casing/synonym
   inventions to their canonical namespaced form
   (`"ROE": "driver:roe"`, `"revenue_growth_YoY": "driver:revenue_growth"`,
   `"material": "material:true"`?). Typed by a new frozen
   `KeyFactorAliases` model in `heuristics.py`. Keep it *small* — most
   recovery is the deterministic namespace coercion (step 2); the alias map
   is only for what coercion can't reach.
3. **News `material:<bool>` clarification.** The prompt advertises
   `material:<bool>` but it is not a `news_vocabulary` list and the model
   emits `material`, `material:bool` literally. Decide (sign-off) whether
   `material:{true,false}` is canonical (then the normaliser accepts those
   two and aliases `material`→`material:true`) or whether `material` is
   dropped from the prompt entirely. Whichever — the vocab/normaliser and
   the prompt must agree, and `config/README.md` must document it.
4. **Fix the stale README claim** at `config/README.md` line 181 ("will fail
   the extractor's closed-vocab check") to describe the *actual* new
   behaviour (coerce/drop + counters), closing the doc-vs-code silent gap.

### 3.4 Why deterministic analysts are out of scope

Technical / Social / SmartMoney build `key_factors` from code-side tag lists
in their extractors (`contract/extractors/{technical,social,smart_money}.py`
— e.g. `key_factors=factors`), not from an LLM, so they cannot emit
off-vocab tags. The normaliser is wired into the **LLM joiners only**. (If a
future audit wants belt-and-braces validation of the deterministic tags too,
that is a separate, cheap follow-up — flagged, not specced here.)

---

## 4. Verification plan

New tests under `tests/` (TDD: write first, watch them fail against today's
no-op behaviour, then implement). The house rule is "assert positive signals,
not just completion" — so tests assert the *specific* coercion/drop outcome,
not merely that the call returns.

### 4.1 Unit — `normalise_key_factors` (the new module)

- **Valid tags pass untouched** (no coercion, no drop): e.g.
  `["risk:macro", "tone:cautious"]` (fundamental) and
  `["catalyst:earnings", "direction:positive"]` (news) round-trip
  identically, `coerced == [] and dropped == []`.
- **Namespace-mismatch is coerced, not dropped** — the dominant news case:
  `["macro", "product_launch"]` (news) → `["catalyst:macro",
  "catalyst:product_launch"]`, with both recorded in `.coerced`. Assert the
  *replacement values*, not just lengths.
- **Risk-suffix coercion**: `["competition_intensified"]` (fundamental) →
  `["risk:competition_intensified"]`.
- **Alias map**: `["ROE", "revenue_growth_YoY"]` → the canonical
  `driver:`-namespaced forms.
- **Extended vocab is now valid**: `["driver:revenue_growth",
  "insider:conviction_sell"]` pass untouched once §3.3 lands (guards the
  vocab extension actually took).
- **Genuine garbage is dropped, counted, and the verdict survives**:
  `["AI_integration", "risk:macro"]` → `.tags == ["risk:macro"]`,
  `.dropped == ["AI_integration"]`.
- **De-duplication**: a tag and its coerced form
  (`["risk:macro", "macro"]`) collapse to one.
- **Ambiguity guard**: a bare value legal under two namespaces is resolved
  deterministically (document the precedence order in the function; assert
  it) — surfaces if any value collides; today none do, but the test pins it.

### 4.2 Integration — joiner end-to-end

- Feed a `temp:{news,fundamental}_verdict_<T>` dict carrying off-vocab tags
  through the real joiner; assert the resulting `VerdictBatch` /
  `fundamental_evidence` carries the **coerced** tags and that a
  `key_factor_coerced` / `key_factor_dropped` log event was emitted
  (capture via the logger). Assert the verdict is **not** turned into
  `is_no_data` (i.e. policy (a) did not fire).
- Assert the persisted `key_factors_json` (via `save_analyst_evidence`)
  contains only valid vocabulary after the fix.

### 4.3 Reporting — counter surfaced in metrics.md

- Unit-test `_collect_obs_aggregates` / `_format_obs_section`
  (`backtest/reporting.py`) with a synthetic `obs/logs` tree containing
  `key_factor_coerced` / `key_factor_dropped` events; assert the returned
  dict carries the two new counters and that the rendered section names
  them. Mirror the existing `hallucinated_stances` reporting test.

### 4.4 Regression — no verdict-pipeline breakage

- Existing joiner/evidence/contract tests still pass (the change is additive
  on the tag field; `lean`/`magnitude`/`confidence`/`report` untouched).
- A clean-vocab verdict produces byte-identical output to today (the
  pass-through branch is a no-op).
- Re-run the analyst eval harness on one window and confirm the scoreboard
  `lean` numbers are unchanged (tags don't feed the scoreboard — this proves
  the fix is hygiene-only, as F-2 predicts).

---

## 5. Implementation surface — one-paragraph summary

Add a pure shared module `src/agents/analysts/key_factors.py` exposing
`normalise_key_factors(tags, *, analyst)` that runs an ordered
pass-through → deterministic suffix-aware namespace coercion → small
config-driven alias map → drop pipeline, reading the closed vocabulary from
`load_heuristics()` and a new `key_factor_aliases` block; wire it into both
LLM joiners (`news/joiner.py`, `fundamental/joiner.py`) at the
`to_ticker_verdict()` boundary so it never raises but emits a structured
`key_factor_coerced` / `key_factor_dropped` log event per change; extend
`config/analyst_heuristics.json` with a new `driver:` namespace
(`drivers` list) plus the missing `insider_signals` /`risks` values that the
model keeps correctly identifying, update `FundamentalVocabulary` (+ a new
`KeyFactorAliases`) in `heuristics.py` and the fundamental prompt's
closed-vocab block accordingly; and aggregate two new counters
(`key_factors_coerced`, `key_factors_dropped`) in `backtest/reporting.py`
next to `hallucinated_stances` so the run's `metrics.md` shows exactly how
much was coerced vs thrown away. No schema `Enum`/`Literal` is added (it
would push the vocabulary into the Vertex output-schema and risk the
documented constrained-decoder pathologies); validation stays in Python at
the joiner choke point, and the policy is loud-but-non-destructive
(coerce/drop + observable counters), never reject-the-verdict.

---

## 6. Open questions for sign-off

1. **Which off-vocab tags become NEW canonical vocabulary?** (The decision
   that splits "adopt" from "drop".) Recommended for adoption, on the
   strength of frequency + the prompt already reasoning about them:
   - **New `driver:` namespace** with values `revenue_growth`,
     `profit_margin`, `free_cash_flow`, `roe` — ~85 % of all invented
     fundamental tags, and the COMPANY-RATIOS reasoning the prompt mandates
     has nowhere else to land. *Strongly recommend adopt.*
   - **`insider_signals` += `discretionary_buy`, `conviction_buy`,
     `conviction_sell`, `neutral`** — the prompt's hard rules explicitly
     discuss conviction buys/sells; the vocab omitting them is a plain gap.
     *Recommend adopt.*
   - **`risks` += `export_control`, `acquisition`** — real, recurrent,
     correctly namespaced (`risk:export_control`). *Recommend adopt.*
   - **Reject (drop-net):** `AI_integration`, `financial_strength`,
     `operational_efficiency`, `capital_allocation`, `investment`,
     `cloud_growth`, `shareholder_returns` — vague, low-frequency, no clean
     analytical meaning. *Recommend drop + count.*
2. **`material:<bool>` — keep or cut?** The news prompt advertises it but it
   isn't a vocab list and the model fumbles it (`material`, `material:bool`).
   Decide: canonicalise as `material:{true,false}` (and alias `material` →
   `material:true`), or remove `material` from the prompt entirely. Either is
   fine; they must agree across prompt + vocab + README.
3. **`driver:` vs folding ratios into an existing namespace.** The spec
   recommends a dedicated `driver:` namespace because these are *drivers*,
   not *risks*; alternative is to overload `tone:`/a generic `factor:`.
   Confirm the namespace name.
4. **Counter as a soft gate?** Should a per-run `key_factors_dropped` above
   some threshold *fail* the eval/acceptance gate (like the golden-set
   gate), or only report? Recommend **report-only** initially — a spike in
   drops is a signal to *review the vocabulary*, not necessarily a run
   failure. Revisit once the vocab extension has settled the baseline drop
   rate near zero.
5. **Alias-map maintenance.** Casing variants (`ROE`/`roe`,
   `revenue_growth_YoY`/`_yoy`) — handle via a small explicit alias map, or
   make the namespace coercion case-insensitive + suffix-normalising? Lean
   toward case-insensitive matching in the coercer to keep the alias map
   tiny; confirm.
