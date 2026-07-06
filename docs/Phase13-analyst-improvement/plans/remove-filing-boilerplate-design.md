# Design: Remove filing boilerplate — MD&A YoY de-boilerplate at LLM-entry

**Phase:** 13 — analyst improvement
**Status:** Design (approved for spec; pending user review before plan)
**Scope this pass:** MD&A only (risk factors deferred to a follow-up pass)

---

## 1. Problem

The Fundamental analyst is ~95% neutral on the eval scoreboard (only ~63
directional verdicts of ~1160). Investigation traced this to a **data-quality**
problem, not a prompt or model problem. Two concrete defects, both confirmed by
code reading:

1. **The prompt asks the LLM to perform a diff it has never had the data for.**
   `select_current_filings` (`src/data/filing_selection.py:83-100`) only ever
   places the **latest** 10-K and the **latest** 10-Q in the prompt dump. The
   prior-year filing is never present. Every instruction that assumes a
   year-over-year comparison — hard rule R2 ("Only a NEW / INTENSIFIED /
   REMOVED bullet … vs the prior filing in the dump counts"), the
   `risk:<value>_added | _removed | _intensified` suffixes, "compare how
   management frames the same topic across the dump", "hedge inflation … in
   passages that previously carried fewer" — has had **no prior filing to
   compare against**. The model has been no-op-ing or hallucinating those rules.

2. **MD&A is truncated twice, both from the front.** Once at fetch
   (`_EXCERPT_CHARS = 2000`, `src/data/providers/filings/edgar.py:159`) and again
   at read-assembly (`max_filing_mda_chars = 1500`,
   `src/agents/analysts/fundamental/fetch.py` context builder). The LLM sees
   ~1500 chars from the **start** of MD&A — which is precisely the
   forward-looking-statements boilerplate preamble, not the substantive
   discussion.

3. **The prior-year filing is not even in the backtest cache.** Backfill mode
   anchors on "the single latest periodic filing as of window start" plus
   in-window filings (`edgar.py:518-522`). For a window shorter than a year the
   prior-year 10-K / 10-Q was filed *before* the anchor and is never fetched.

The fix is therefore larger than "de-boilerplate MD&A at read": it is
**cache full prose for two generations → supply N-1 at the read layer → diff at
LLM-entry**, keeping the *current* narrative minus provably-dead boilerplate.

This aligns with "Lazy Prices" (Cohen, Malloy & Nguyen, RFS 2020): year-over-year
changes in filing language predict returns. We are not (this pass) computing a
signed change signal — we are **subtractively de-boilerplating** so the
analyst's limited attention lands on the paragraphs that are new or revised.

---

## 2. Scope

**In scope:** MD&A (Item 7 in 10-K, Item 2 in 10-Q).

**Out of scope this pass:**
- Risk-factor `_added / _removed / _intensified` change-tagging. Same N-1
  plumbing will serve it later, but it is a different transform (delta tagging,
  not subtractive de-boilerplate) and is deferred to a follow-up pass. We will
  cache full risk-factor prose now (free) so that pass needs no refetch.
- 8-K `body_excerpt` handling (its cap is a separate constant, untouched).
- The heavy "redline change-detector" reconception of the Fundamental analyst —
  backlogged, its own brainstorming session.

**Success test:** refetch the `iran-conflict` window, re-run the analyst-eval
iteration, and confirm the **fundamental predictive power moves** vs iter-3 —
measured as mean excess return (bps) and directional-call count against the
~95%-neutral baseline.

---

## 3. Decisions (locked in brainstorming)

| # | Decision | Choice |
|---|----------|--------|
| Scope | Which sections | **MD&A only** this pass; risk factors deferred |
| N-1 (10-K) | Diff baseline | Prior year's 10-K |
| N-1 (10-Q) | Diff baseline | **Same fiscal quarter, prior year** (Lazy-Prices canonical; seasonal language matches and is stripped) |
| Fallback | Diff can't run | **Full prose + loud marker**, never silent, never dropped; diff coverage logged to trace |
| Post-diff cap | Survivor text to LLM | **Generous safety cap (~12k chars)**, truncating from the **tail** (survivors are signal-dense and ordered) with a loud marker |
| Memoisation | Where the diff lives | **In-process LRU**, keyed `(acc_N, acc_N-1, algo_version)`; **no schema change** — golden cache stays raw-only (cache-raw-transform-at-read) |
| Refetch scope | First proof | **iran-conflict only**; expand to baselines if the eval moves |

---

## 4. Components

### 4.1 Full-prose caching (fetch side)

`src/data/providers/filings/edgar.py` — `_section_text` currently returns
`text[:_EXCERPT_CHARS]`. **Remove the truncation** so the complete MD&A and
risk-factor prose is cached verbatim. SQLite columns are already `Text`
(`src/backtest/cache/schema.py` `FilingRow`), so **no schema migration**.

- Applies to both `mda_excerpt` and `risk_factors_excerpt` (risk is not diffed
  this pass, but caching it full now avoids a second refetch later).
- 8-K `body_excerpt` keeps its `_BODY_EXCERPT_CHARS` cap — out of scope.
- `_EXCERPT_CHARS` is either removed or retained only as a defensive upper
  sanity bound far above any real filing (decide in plan; default: remove and
  rely on the post-diff cap downstream).

### 4.2 Extended backfill — two generations

`edgar.py` backfill mode (`from_date` given). The periodic anchor currently
fetches only the single latest 10-K / 10-Q as of `window_lower`. **Extend the
periodic anchor reach to ~400 days before `window_lower`** so the cache holds:

- the prior-year 10-K, and
- the same-quarter-prior-year 10-Q (~4 quarters back).

Backfill-only change. The in-window range query and the 8-K staleness pane are
unchanged. The ~400-day figure is a guard band over a clean 365 days to absorb
late filings; pin the exact constant in the plan.

### 4.3 N-1 supply at read — the parity-critical seam

The diff needs N-1 prose at context-assembly in **both** paths, which are
asymmetric:

- **Backtest:** `store.read_filings(ticker, as_of)` already returns the full
  unbounded history (`filed_at <= as_of`, no lower bound,
  `src/backtest/cache/store.py`). N-1 is already in hand. **No store change.**
- **Live:** `edgar.fetch` live-mode pre-narrows via `select_current_filings` to
  the latest only, dropping N-1 before assembly sees it.

**Contract:** the fundamental read path consumes a filing **pool** (current
visible filings + prior-year baselines); selection-for-rendering and pairing
happen at the **assembly** layer, not in the provider.

- The assembly pairs each *rendered* current periodic filing with its prior-year
  baseline by **fiscal period** (`period_of_report` / fiscal-period attribute,
  **not** filing date — a late-filed quarter must still pair correctly).
- Only the **current** filing is rendered (de-boilerplated). A baseline filing
  is consumed solely as a diff input and is **never** rendered as its own block.
- Backtest obtains the pool free from `read_filings`. Live `edgar.fetch` gains a
  baseline query mirroring the §4.2 reach so the prior-year periodic filing's
  prose is available; the single provider signature is preserved (the provider
  returns enough history for the assembly to pair).

This seam is the **highest risk to live/replay parity** — the property the
`edgar.py` header docstring guards. It is covered by a dedicated parity test
(§4.8).

### 4.4 The de-boilerplate algorithm

A pure function, version-stamped by an `algo_version` **code constant** (not
config — it is a code-coupled identifier; bumping it invalidates the LRU).

```
deboilerplate_mda(current_text, prior_text, algo_version) -> (survivor_text, stats)
```

Steps:

1. **Split** `current_text` and `prior_text` into paragraphs on blank lines
   (`\n\n+`); if a document has no blank-line delimiters, fall back to single
   `\n`.
2. For each paragraph compute a **normalised hash key**: lowercase, strip
   punctuation, collapse all whitespace to single spaces, trim. Hash with
   SHA-256. (Normalisation is for the **key only** — the original paragraph text
   is preserved for output.)
3. Build the set of prior-paragraph hashes.
4. **Keep** each current paragraph whose hash is **not** in the prior set,
   **in original document order**.
5. Rejoin survivors with blank lines.

**Exact identity only.** No fuzzy / cosine / Jaccard dropping — that would
discard "same wording, new numbers" paragraphs, which are exactly the signal
(normalisation does *not* strip digits, so a changed figure changes the hash and
the paragraph survives).

6. Prepend a **header line** so the model knows it is reading a filtered view,
   e.g.: `[MD&A: paragraphs new or revised vs <prior period, e.g. 2024-Q3>; N
   identical boilerplate paragraphs removed.]`

7. **Memoise** on `(acc_N, acc_N-1, algo_version)` via an in-process LRU.

`stats` carries: chars in, chars out, paragraphs dropped, coverage % — for trace
logging (§4.6).

### 4.5 Fallbacks, stub detection, post-diff cap (loud-never-silent)

Per `feedback_silent_failures_loud_tests` — every degenerate path is loud and
preserves data:

- **No N-1 available** (prior-year filing absent, newly-listed company): render
  the **full** current MD&A un-stripped, prefixed with
  `[MD&A in full — no prior-year filing to compare; not de-boilerplated.]`
- **Incorporated-by-reference stub** (e.g. XOM 10-K Item 7 is a ~265-char
  "Reference is made to … Exhibit 13" pointer — see
  `project_xom_10k_mda_incorporated_by_reference` memory): detect via short
  length (`< mda_stub_char_threshold`) **and** a cross-reference phrase match
  ("reference is made to" / "incorporated by reference"). Render no body, marker
  `[MD&A incorporated by reference — see 10-Q for prose.]`. The real signal for
  such filers comes from the 10-Q.
- **Post-diff overflow:** if survivor text exceeds `max_filing_mda_chars`
  (~12k), keep the **head**, drop the **tail**, append
  `[MD&A truncated: N chars of de-boilerplated narrative omitted.]`. The cap
  bites the least-important tail, never the preamble — this is categorically
  different from the original front-truncation bug.

### 4.6 Tracing

Per filing, emit to the trace: whether the diff **fired** vs **fell back** (and
which fallback), and the coverage % (chars stripped). This makes the mechanism
**measurable** — we can count how often the diff actually runs across a window,
not just assume it does.

### 4.7 Config (`config/analysts.json` `fundamental` block + `config/README.md`)

- `max_filing_mda_chars`: **1500 → 12000** — re-purposed as the **post-diff
  survivor cap** (tail-truncation), no longer a front cap. (`FundamentalCaps`
  already bounds it `le=20_000`, so 12000 is in range.)
- `mda_stub_char_threshold`: **new**, ~400 — incorporated-by-reference stub
  detection length.
- `algo_version`: **code constant, not config** — code-coupled identifier.
- Update `config/README.md` per the configuration convention.

### 4.8 Prompt update (`src/agents/analysts/fundamental/prompts.py`)

The subtractive diff means the LLM now sees **what is new/revised**, not old-vs-
new side by side. Update accordingly:

- `-- COMPANY FILINGS (PROSE) --` description: state that the MD&A shown is the
  de-boilerplated narrative (paragraphs new or revised vs the prior-year same-
  period filing), and explain the three markers (full/un-stripped, stub,
  truncated).
- MD&A-reading guidance (currently "compare how management frames the same topic
  across the dump", verb-commitment, hedge-density): reframe to "the MD&A shown
  is **only** the new or revised narrative vs the prior-year filing; treat its
  presence and content as the signal" — the model no longer has both versions to
  compare, it has the *delta narrative*.
- Risk-factor `_added / _removed / _intensified` instructions and R2 stay
  **dormant** (risk factors are not diffed this pass and still arrive
  un-compared). We will trim or wire them in the risk pass — flagged, not
  touched now, to keep this change minimal.

### 4.9 Validation

1. **Refetch** `iran-conflict` with the extended backfill (full prose, two
   generations).
2. **Re-run** the analyst-eval iteration; compare fundamental mean excess return
   (bps) and directional-call count vs iter-3.
3. **Unit tests:** paragraph split (blank-line + single-newline fallback);
   normalisation (case/punct/whitespace, digits preserved); exact-hash drop;
   survivor ordering; stub detection (length + phrase); no-N-1 fallback;
   tail-truncation cap + marker; fiscal-period pairing (incl. late-filed
   quarter); LRU keying incl. `algo_version` invalidation.
4. **Parity test:** the same fixture filing pair produces **identical**
   de-boilerplated MD&A through the live path and the replay/cache path
   (guards §4.3).

---

## 5. What this deliberately does *not* do (YAGNI)

- No persistent diff-cache table (in-process LRU only; the compute is sub-ms per
  filing, so cross-run recompute is cheap and a derived-data table would depart
  from cache-raw).
- No fuzzy/semantic paragraph matching (exact identity only).
- No signed change signal / sentiment delta (subtractive de-boilerplate only).
- No risk-factor diffing, no 8-K changes, no schema migration.

---

## 6. Open implementation details for the plan (not design decisions)

- Exact backfill reach constant (~400 days) and whether `_EXCERPT_CHARS` is
  removed or retained as a far-above-real sanity bound.
- The precise provider/assembly seam in §4.3 that yields the filing *pool* to
  the fundamental read path while preserving the single provider signature and
  live/replay parity.
- The `period_of_report` / fiscal-period attribute name exposed by edgartools
  for fiscal-period pairing.
- LRU size / eviction policy.
