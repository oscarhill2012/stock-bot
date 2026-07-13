# Plan 1b — Filing-Similarity Rework (faithful "Lazy Prices" measurement)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Sequencing:** Lands *after* Plan 3 (news-drift rebuild) and *before* Plans 4–5.
It **gates the backtest eval**: the eval was held because the Plan 1 fundamental
signal is degenerate (see Motivation). Numbered `1b` for lineage — it revises
Plan 1's measurement without renumbering Plans 2–5 (avoids churn in the
by-number cross-references inside plan1/plan3).

**Goal:** Make the fundamental analyst *measure* the Cohen–Malloy–Nguyen effect
instead of only borrowing its sign — replace the exact-match, digit-preserving
paragraph diff (which retains ~55–70% of every filing and fires "lots changed →
bearish" on nearly every name) with a lexical similarity primitive (cosine +
Jaccard, number-normalised), a `filing_diff` that drops *near*-duplicate
paragraphs and separately surfaces large numeric deltas, per-filing cosines
precomputed at fetch time and persisted to the golden cache, and a self-relative
scale summary that tells the LLM where this filing sits within *the same firm's
own* year-over-year history.

**Architecture:** One shared, pure similarity primitive (`filing_similarity`)
is called by three consumers — the tick-time `filing_diff`, a fetch-phase
precompute pass that persists each filing's section cosine to the cache, and a
deterministic `scale_summary` that bands the current cosine against the firm's
own history. The backtest is fetch-once / replay-many, so all similarity
*computation* happens in the fetch phase; the tick-time path only *reads*
persisted scalars — identical to every other cache-backed provider. Direction
comes from the diff *content*; magnitude comes from the similarity *scale*; the
CMN prior lives in the prompt.

**Tech Stack:** Python 3.14, Pydantic v2, SQLAlchemy (SQLite golden cache),
Google ADK per-ticker fan-out, pytest. Lexical bag-of-words only — no neural
embeddings (CMN is lexical; embeddings deferred).

## Global Constraints

- **British English** everywhere (colour, behaviour, normalise, analyse,
  optimise) — identifiers, comments, prose.
- **Every function gets a docstring** (purpose, parameters, return). Comment
  non-trivial logic; blank lines between logical blocks.
- **One shared scorer, both paths.** No backtest-only similarity script. The
  golden-cache scalar columns are *memoisation* of the shared function's output,
  exactly like `report_cache`.
- **Algo-version stamps.** `FILING_SIMILARITY_ALGO_VERSION` and
  `FILING_DIFF_ALGO_VERSION` feed the `fundamental_hash_inputs` digest (bust the
  report cache) AND gate the persisted cosine columns (a version change forces
  recompute on refetch — never a silent stale read; this is the
  `--refetch-domain` no-op trap from the revenue-concept fix).
- **Additive-nullable cache migration.** New cosine columns are nullable `Float`
  on `FilingRow`, self-healing via `_migrate_additive_columns` — **no
  `SCHEMA_VERSION` bump, no cache wipe** (the Plan 1 `litigation_excerpt`
  pattern).
- **PIT.** The self-relative series is built only from filings the provider
  already as_of-slices (`filed_at <= as_of`). No future similarity enters the
  window. `as_of` reads use `resolve_as_of`; datetimes written to session state
  are ISO-stringified first.
- **Config, not constants.** Every threshold lives in `config/analysts.json` and
  is documented in `config/README.md`. No hardcoded config in source.
- **Loud, not silent.** Prefer raises over null/empty/neutral degradation. A
  missing prior pair is a known branch (excerpt-only marker); a *computation*
  failure raises. Tests assert the positive signal (a bullish leg appears; a
  real cosine comes back), not merely "no error".
- **Rename is bundled.** `deboilerplate.py → filing_diff.py` happens *inside* the
  algorithm-change task (Task 3), not as a separate mechanical commit.
- **Shell:** run from project root, no `cd` prefix, e.g.
  `.venv/bin/python -m pytest tests/ -q` and
  `PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch ...`.

---

## Motivation (read once)

The spot-check run (`backtests/long-baseline-2025/runs/first-month-1/`, 2 ticks,
20 tickers) collapsed to one pole: **18/20 bearish**, magnitude 0.6–0.75, ~zero
bullish; paragraph retention **55–70%** on every large section. Root cause: Plan
1's diff is an exact-match SHA-256 fingerprint that **preserves digits**, so
every routine numeric roll-forward (revenue, dates, percentages) makes a
paragraph "unique" and it survives. Retention never approaches the near-verbatim
floor, so the quiet-bullish leg is **unreachable by construction**. CMN measured
*language similarity* (four document-level lexical measures — cosine, Jaccard,
min-edit, simple — bag-of-words, not embeddings). We reproduce the measurement
and give each firm a faithful, self-relative scale, restoring a reachable
bullish leg and grounding magnitude in a real number rather than a retention
artefact.

**Non-goal (v1):** cross-sectional / sector-corpus percentiles. We accept the
chronic-changer blind spot (a habitual churner normalises away under a purely
self-relative scale) — direction still comes from the diff *content*, so its
actual changes are surfaced regardless. The absolute anchor is deferred to the
backlog.

---

## File structure

| File | Responsibility |
|------|----------------|
| `src/agents/analysts/fundamental/filing_similarity.py` (new) | Pure lexical primitive: number-normalise → bag-of-words → cosine + Jaccard. Owns `FILING_SIMILARITY_ALGO_VERSION`. |
| `src/agents/analysts/fundamental/filing_diff.py` (rename of `deboilerplate.py`) | Tick-time paragraph dedup by *cosine threshold* + numeric-delta detector. Owns `FILING_DIFF_ALGO_VERSION`. |
| `src/agents/analysts/fundamental/scale_summary.py` (new) | Deterministic self-relative band: current cosine vs the firm's own history → one summary line. |
| `src/agents/analysts/fundamental/fetch.py` (modify) | Compute-and-persist helper; wire the scale summary into the LLM context. |
| `src/agents/analysts/report_cache.py` (modify) | Swap the algo-version import; add the similarity version to the fundamental digest. |
| `src/backtest/cache/schema.py` + `store.py` (modify) | Six nullable `Float` cosine/Jaccard columns on `FilingRow`; write + read. |
| `src/data/models/filings.py` (modify) | Six matching nullable float fields on `Filing` so the scalars round-trip. |
| `src/data/providers/filings/edgar.py` + `fetch_agent.py` (modify) | Widen the baseline-pool reach from 800 days to `filing_history_years`. |
| `scripts/backtest_fetch` (modify) | Invoke the precompute pass after filings are written. |
| `src/config/analysts.py` + `config/analysts.json` + `config/README.md` (modify) | New `FundamentalCaps` settings. |
| `src/agents/analysts/fundamental/prompts.py` (modify) | Diff-content direction + scale-driven magnitude; kill the volume heuristic. |

---

## Task 1: Config — similarity thresholds, history horizon, scale bands

**Files:**
- Modify: `src/config/analysts.py` (class `FundamentalCaps`)
- Modify: `config/analysts.json` (`fundamental` block)
- Modify: `config/README.md`
- Test: `tests/unit/config/test_analysts_config.py`

**Interfaces:**
- Produces: `FundamentalCaps.filing_dedup_cosine: float`,
  `.filing_numeric_delta_pct: float`, `.filing_history_years: int`,
  `.filing_scale_high_pct: float`, `.filing_scale_low_pct: float`,
  `.filing_scale_min_history: int` — consumed by Tasks 3, 5, 6, 7.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/config/test_analysts_config.py`:

```python
def test_fundamental_filing_similarity_settings_load() -> None:
    """The Phase 14 1b filing-similarity settings must load and validate.

    A missing field must RAISE (loud) rather than silently defaulting — the
    silent-degradation failure mode this project treats as its recurring bug
    class.  We assert the concrete configured values, not just presence.
    """
    caps = get_analysts_config().fundamental

    assert 0.0 <= caps.filing_dedup_cosine <= 1.0
    assert caps.filing_numeric_delta_pct > 0.0
    assert 1 <= caps.filing_history_years <= 15
    assert 0.0 <= caps.filing_scale_low_pct < caps.filing_scale_high_pct <= 1.0
    assert caps.filing_scale_min_history >= 1
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py::test_fundamental_filing_similarity_settings_load -v`
Expected: FAIL — `AttributeError` / `ValidationError` (fields absent on `FundamentalCaps`).

- [ ] **Step 3: Add the fields to `FundamentalCaps`**

In `src/config/analysts.py`, inside `class FundamentalCaps(BaseModel)`, after
`filing_delta_horizon_days`:

```python
    # ---------------------------------------------------------------------
    # Phase 14 Plan 1b — filing-similarity (faithful "Lazy Prices")
    # ---------------------------------------------------------------------
    # Two current-year paragraphs count as the "same" paragraph (dropped from
    # the diff body) when their number-normalised bag-of-words cosine meets or
    # exceeds this.  0.92 keeps genuine rewrites while collapsing pure numeric
    # roll-forwards — the exact defect behind the degenerate 18/20-bearish run.
    filing_dedup_cosine: float = Field(ge=0.0, le=1.0, default=0.92)

    # A figure inside an otherwise-deduplicated paragraph is surfaced to the LLM
    # when |Δ| / prior meets or exceeds this fraction (10% => a 12.1 -> 13.4
    # revenue change is flagged even though number-normalisation hid it from the
    # similarity view).
    filing_numeric_delta_pct: float = Field(gt=0.0, default=0.10)

    # How many years of the firm's OWN prior filings form the self-relative
    # similarity series.  Drives the baseline-pool reach (Task 5).  7 years of
    # 10-Qs is ~28 points; of 10-Ks ~7 points.  Config-toggleable.
    filing_history_years: int = Field(ge=1, le=15, default=7)

    # Percentile bands (of the current cosine within the firm's own history)
    # that the scale summariser turns into words.  A current cosine in the TOP
    # band (>= high) means "this filing changed LESS than usual for this firm"
    # (quiet-bullish tilt); BOTTOM band (<= low) means "changed MORE than usual"
    # (bearish tilt).
    filing_scale_high_pct: float = Field(ge=0.0, le=1.0, default=0.80)
    filing_scale_low_pct:  float = Field(ge=0.0, le=1.0, default=0.20)

    # Below this many prior points the percentile is not trustworthy — the
    # summariser emits an honest thin-history hedge instead of a false-precision
    # band.
    filing_scale_min_history: int = Field(ge=1, default=3)
```

- [ ] **Step 4: Add the values to `config/analysts.json`**

In the `fundamental` object, after `"filing_delta_horizon_days": 60,`:

```json
    "filing_dedup_cosine": 0.92,
    "filing_numeric_delta_pct": 0.10,
    "filing_history_years": 7,
    "filing_scale_high_pct": 0.80,
    "filing_scale_low_pct": 0.20,
    "filing_scale_min_history": 3,
```

- [ ] **Step 5: Document in `config/README.md`**

Under the `analysts.json` → `fundamental` section, add one bullet per setting
describing its meaning and valid range (mirror the docstrings above).

- [ ] **Step 6: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/config/analysts.py config/analysts.json config/README.md tests/unit/config/test_analysts_config.py
git commit -m "feat(config): filing-similarity thresholds, history horizon, scale bands (Phase 14 1b)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Shared similarity primitive (`filing_similarity.py`)

**Files:**
- Create: `src/agents/analysts/fundamental/filing_similarity.py`
- Test: `tests/unit/agents/analysts/fundamental/test_filing_similarity.py`

**Interfaces:**
- Produces: `FILING_SIMILARITY_ALGO_VERSION: str`;
  `@dataclass(frozen=True) class SimilarityScores: cosine: float; jaccard: float`;
  `compute_similarity(current: str, prior: str) -> SimilarityScores` (memoised,
  pure). Consumed by Tasks 3, 5, 6.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the lexical filing-similarity primitive."""
from __future__ import annotations

from agents.analysts.fundamental.filing_similarity import (
    SimilarityScores,
    compute_similarity,
)


def test_identical_text_scores_one() -> None:
    """Identical prose is a perfect match on both measures."""
    text = "The company grew revenue and expanded margins in all regions."
    scores = compute_similarity(text, text)
    assert scores.cosine == 1.0
    assert scores.jaccard == 1.0


def test_number_only_rollforward_is_near_verbatim() -> None:
    """A pure figure roll-forward must read as near-identical.

    This is the exact case the old digit-preserving hash treated as fully
    changed — the defect behind the degenerate all-bearish run.  Under number
    normalisation the two paragraphs are the same language.
    """
    prior   = "Revenue was 12.1 billion, up 4% year over year."
    current = "Revenue was 13.4 billion, up 9% year over year."
    scores = compute_similarity(current, prior)
    assert scores.cosine > 0.95


def test_substantial_rewrite_scores_low() -> None:
    """Genuinely different prose scores low on cosine."""
    prior   = "We expect continued strong demand across our core markets."
    current = "A newly disclosed regulatory investigation may materially harm results."
    scores = compute_similarity(current, prior)
    assert scores.cosine < 0.4


def test_empty_versus_nonempty_is_zero_not_nan() -> None:
    """One-sided emptiness is a clean 0.0, never NaN."""
    scores = compute_similarity("", "some real content here")
    assert scores.cosine == 0.0
    assert scores.jaccard == 0.0
```

- [ ] **Step 2: Run it to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_filing_similarity.py -v`
Expected: FAIL — module `filing_similarity` does not exist.

- [ ] **Step 3: Write the implementation**

Create `src/agents/analysts/fundamental/filing_similarity.py`:

```python
"""Lexical filing-similarity primitive — number-normalised bag-of-words.

Implements the measurement behind Cohen, Malloy & Nguyen (2020, "Lazy Prices"):
document (or paragraph) similarity via lexical measures, NOT neural embeddings.
Two measures are returned — cosine (term-frequency vector) and Jaccard (token
set) — because CMN used several and a second view is cheap.

The critical departure from the Phase 13 SHA-256 diff is NUMBER NORMALISATION:
every numeral run is collapsed to a single placeholder token before vectorising,
so a routine figure roll-forward (revenue 12.1 -> 13.4) no longer makes an
otherwise-identical paragraph look "fully changed".  Figure changes are surfaced
separately by ``filing_diff`` (Task 3), not through this similarity view.

Pure and memoised on the ``(current, prior)`` string pair — safe because both
arguments are immutable.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from math import sqrt

# Bump when the tokenisation / normalisation / scoring changes.  Feeds the
# fundamental report-cache digest AND gates the persisted cosine columns so a
# change forces recompute on refetch (never a silent stale read).
FILING_SIMILARITY_ALGO_VERSION = "v1"

# A maximal run of digits with embedded separators / percent signs — collapsed
# to one placeholder token so numeric drift does not dominate the vector.
_RE_NUMBER = re.compile(r"\d[\d,.%]*")

# Non-word, non-space characters stripped after number normalisation.
_RE_PUNCT = re.compile(r"[^\w\s]")

# Whitespace collapse.
_RE_SPACE = re.compile(r"\s+")

# The single token every numeral collapses to.  Deliberately not a real word.
_NUM_TOKEN = " qnum "


@dataclass(frozen=True)
class SimilarityScores:
    """A pair of lexical similarity measures in [0.0, 1.0].

    Attributes
    ----------
    cosine:
        Term-frequency cosine similarity — the primary measure (drives the
        self-relative scale).
    jaccard:
        Token-set Jaccard similarity — a secondary view shown to the LLM.
    """

    cosine:  float
    jaccard: float


def _tokenise(text: str) -> list[str]:
    """Normalise and tokenise filing prose into comparable tokens.

    Lowercases, collapses every numeral run to a single placeholder token,
    strips punctuation, and splits on whitespace.  Number normalisation is the
    whole point: it makes a figure roll-forward lexically identical to its prior
    year so it stops masquerading as substantive change.

    Parameters
    ----------
    text:
        Raw section or paragraph prose.

    Returns
    -------
    list[str]
        Lower-cased tokens in document order (may be empty).
    """
    lowered   = text.lower()
    numbered  = _RE_NUMBER.sub(_NUM_TOKEN, lowered)
    no_punct  = _RE_PUNCT.sub(" ", numbered)
    collapsed = _RE_SPACE.sub(" ", no_punct).strip()

    return collapsed.split(" ") if collapsed else []


def _cosine(a_counts: Counter[str], b_counts: Counter[str]) -> float:
    """Cosine similarity of two term-frequency Counters.

    Returns 0.0 when either vector is empty (one-sided emptiness), never NaN.

    Parameters
    ----------
    a_counts, b_counts:
        Term-frequency Counters over normalised tokens.

    Returns
    -------
    float
        Cosine similarity in [0.0, 1.0].
    """
    if not a_counts or not b_counts:
        return 0.0

    # Dot product over the shared vocabulary.
    shared = set(a_counts) & set(b_counts)
    dot    = sum(a_counts[t] * b_counts[t] for t in shared)

    norm_a = sqrt(sum(v * v for v in a_counts.values()))
    norm_b = sqrt(sum(v * v for v in b_counts.values()))

    return dot / (norm_a * norm_b)


def _jaccard(a_tokens: set[str], b_tokens: set[str]) -> float:
    """Jaccard similarity of two token sets.

    Returns 0.0 when either set is empty.

    Parameters
    ----------
    a_tokens, b_tokens:
        Sets of normalised tokens.

    Returns
    -------
    float
        Jaccard similarity in [0.0, 1.0].
    """
    if not a_tokens or not b_tokens:
        return 0.0

    union = a_tokens | b_tokens
    return len(a_tokens & b_tokens) / len(union)


@lru_cache(maxsize=4096)
def compute_similarity(current: str, prior: str) -> SimilarityScores:
    """Return the lexical similarity of two pieces of filing prose.

    Pure and memoised on ``(current, prior)`` — both immutable strings.  Number
    normalisation is applied inside ``_tokenise`` so figure roll-forwards do not
    register as change.

    Parameters
    ----------
    current:
        The current filing's section (or paragraph) text.
    prior:
        The prior comparable filing's counterpart text.

    Returns
    -------
    SimilarityScores
        ``cosine`` (primary) and ``jaccard`` (secondary), both in [0.0, 1.0].
    """
    a_tokens = _tokenise(current)
    b_tokens = _tokenise(prior)

    cosine  = _cosine(Counter(a_tokens), Counter(b_tokens))
    jaccard = _jaccard(set(a_tokens), set(b_tokens))

    return SimilarityScores(cosine=cosine, jaccard=jaccard)
```

- [ ] **Step 4: Run the test to see it pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_filing_similarity.py -v`
Expected: PASS (all four).

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/filing_similarity.py tests/unit/agents/analysts/fundamental/test_filing_similarity.py
git commit -m "feat(fundamental): lexical filing-similarity primitive (cosine + Jaccard, number-normalised)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: `filing_diff` — rename `deboilerplate` + similarity-threshold dedup + numeric deltas

**Files:**
- Rename: `src/agents/analysts/fundamental/deboilerplate.py` → `filing_diff.py`
- Rename: `tests/unit/agents/analysts/fundamental/test_deboilerplate.py` → `test_filing_diff.py`
- Modify: `src/agents/analysts/fundamental/fetch.py` (imports + `_render_diffed_section`)
- Modify: `src/agents/analysts/report_cache.py` (import + digest payload)

**Interfaces:**
- Consumes: `compute_similarity`, `FILING_SIMILARITY_ALGO_VERSION` (Task 2);
  `FundamentalCaps.filing_dedup_cosine`, `.filing_numeric_delta_pct` (Task 1).
- Produces: `FILING_DIFF_ALGO_VERSION: str`;
  `filing_diff(current_text: str, prior_text: str, *, dedup_cosine: float,
  numeric_delta_pct: float, algo_version: str, prior_period_label: str)
  -> tuple[str, dict]`. Consumed by `fetch.py` and `report_cache.py`.

- [ ] **Step 1: `git mv` the module and its test (behaviour change follows)**

```bash
git mv src/agents/analysts/fundamental/deboilerplate.py src/agents/analysts/fundamental/filing_diff.py
git mv tests/unit/agents/analysts/fundamental/test_deboilerplate.py tests/unit/agents/analysts/fundamental/test_filing_diff.py
```

- [ ] **Step 2: Write the failing tests (new behaviour)**

Replace the body of `tests/unit/agents/analysts/fundamental/test_filing_diff.py`
with tests for the new similarity-threshold behaviour (keep any still-valid
paragraph-split tests, updating the import to `filing_diff`):

```python
"""Tests for filing_diff — similarity-threshold dedup + numeric-delta surfacing."""
from __future__ import annotations

from agents.analysts.fundamental.filing_diff import (
    FILING_DIFF_ALGO_VERSION,
    filing_diff,
)

_DEDUP = 0.92
_NUM_PCT = 0.10


def _run(current: str, prior: str):
    return filing_diff(
        current, prior,
        dedup_cosine=_DEDUP,
        numeric_delta_pct=_NUM_PCT,
        algo_version=FILING_DIFF_ALGO_VERSION,
        prior_period_label="FY2023",
    )


def test_number_only_rollforward_dedups_to_near_verbatim() -> None:
    """A filing that only rolls numbers forward must read as near-verbatim.

    This is the quiet-bullish leg Plan 1 could never reach.  Every paragraph is
    a numeric roll-forward of the prior year, so all should dedup and the
    near-verbatim marker must fire with NO body prose.
    """
    prior = (
        "Revenue was 12.1 billion, up 4% year over year.\n\n"
        "Operating margin improved to 21.0% from 19.5%."
    )
    current = (
        "Revenue was 13.4 billion, up 9% year over year.\n\n"
        "Operating margin improved to 23.0% from 21.0%."
    )
    text, stats = _run(current, prior)
    assert "near-verbatim" in text
    assert stats["paragraphs_dropped"] == stats["paragraphs_total"]


def test_numeric_delta_is_surfaced_even_when_paragraph_deduped() -> None:
    """A large figure change inside a deduped paragraph must be surfaced.

    Number normalisation hides it from the similarity view; the numeric-delta
    detector must bring it back so the LLM can weigh it.
    """
    prior   = "Total contractual obligations were 1.0 billion at year end."
    current = "Total contractual obligations were 3.0 billion at year end."
    text, stats = _run(current, prior)
    assert stats["numeric_deltas"], "a >=10% figure change must be recorded"
    assert "NUMERIC DELTAS" in text


def test_genuine_rewrite_survives_the_diff() -> None:
    """Substantively new prose must NOT be deduped — it is the bearish signal."""
    prior   = "We expect continued strong demand across our core markets."
    current = "A newly disclosed regulatory investigation may materially harm results."
    text, stats = _run(current, prior)
    assert stats["paragraphs_dropped"] == 0
    assert "regulatory investigation" in text
```

- [ ] **Step 3: Run to see them fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_filing_diff.py -v`
Expected: FAIL — `filing_diff` symbol/behaviour absent (module still exposes the
old `deboilerplate_mda`).

- [ ] **Step 4: Rewrite `filing_diff.py`**

Replace the module contents. Keep `_split_paragraphs` (unchanged). Replace the
SHA-256 fingerprinting with cosine-threshold dedup and add the numeric-delta
detector:

```python
"""filing_diff — year-over-year paragraph diff by lexical similarity.

Supersedes the Phase 13 SHA-256 de-boilerplate filter.  A current paragraph is
dropped as a near-duplicate when its number-normalised cosine against its
best-matching prior-year paragraph meets ``dedup_cosine``.  Two additions over
a plain drop:

1. NUMERIC DELTAS — a paragraph deduped on language may still carry a materially
   changed figure (number normalisation hides it).  We compare the raw numerals
   of the current paragraph against its matched prior paragraph and surface any
   change >= ``numeric_delta_pct`` so the LLM can weigh it.
2. Near-verbatim marker — when (almost) every paragraph dedups, the documented
   "filing is near-verbatim" header fires with no body, landing the LLM in the
   quiet-bullish branch.

Pure; the LLM-facing text is deterministic given the inputs and thresholds.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from agents.analysts.fundamental.filing_similarity import (
    FILING_SIMILARITY_ALGO_VERSION,
    compute_similarity,
)

_logger = logging.getLogger(__name__)

# Bump when the diff assembly changes.  Combined with the similarity version so
# either moving busts the fundamental report cache and the persisted columns.
FILING_DIFF_ALGO_VERSION = f"v2+sim:{FILING_SIMILARITY_ALGO_VERSION}"

_RE_BLANK_LINES = re.compile(r"\n\n+")

# A signed decimal numeral (with thousands separators) for the numeric-delta
# detector.  Percent signs are excluded from the capture so "21.0%" yields 21.0.
_RE_NUMERAL = re.compile(r"[-+]?\d[\d,]*\.?\d*")


def _split_paragraphs(text: str) -> list[str]:
    """Split ``text`` into non-empty paragraphs on blank-line boundaries.

    Falls back to single-newline splitting when the text has no blank lines
    (common in some EDGAR extracts) so the diff still operates on chunks rather
    than the whole document.

    Parameters
    ----------
    text:
        Raw section prose.

    Returns
    -------
    list[str]
        Stripped, non-empty paragraphs in document order.
    """
    paragraphs = _RE_BLANK_LINES.split(text)
    if len(paragraphs) <= 1:
        paragraphs = text.split("\n")
    return [p.strip() for p in paragraphs if p.strip()]


def _numerals(paragraph: str) -> list[float]:
    """Extract comparable numerals from a paragraph in document order.

    Parameters
    ----------
    paragraph:
        A single paragraph of prose.

    Returns
    -------
    list[float]
        Parsed numerals (thousands separators stripped); empty if none.
    """
    out: list[float] = []
    for raw in _RE_NUMERAL.findall(paragraph):
        try:
            out.append(float(raw.replace(",", "")))
        except ValueError:
            continue
    return out


def _numeric_deltas(current_para: str, prior_para: str, threshold: float) -> list[str]:
    """Return human-readable notes for materially changed figures.

    Compares the ordered numerals of a deduped current paragraph against its
    matched prior paragraph.  Only aligned when the counts match (a differing
    count means the structure changed, in which case the paragraph would not
    have deduped anyway).  A change qualifies when ``|Δ| / |prior|`` meets
    ``threshold``.

    Parameters
    ----------
    current_para, prior_para:
        The matched paragraph pair.
    threshold:
        Minimum fractional change to surface.

    Returns
    -------
    list[str]
        Notes like ``"1.0 -> 3.0 (+200.0%)"``; empty if nothing qualifies.
    """
    cur = _numerals(current_para)
    pri = _numerals(prior_para)
    if not cur or len(cur) != len(pri):
        return []

    notes: list[str] = []
    for c, p in zip(cur, pri, strict=True):
        if p == 0.0:
            continue
        change = (c - p) / abs(p)
        if abs(change) >= threshold:
            notes.append(f"{p:g} -> {c:g} ({change * 100:+.1f}%)")
    return notes


@lru_cache(maxsize=256)
def filing_diff(
    current_text: str,
    prior_text: str,
    *,
    dedup_cosine: float,
    numeric_delta_pct: float,
    algo_version: str = FILING_DIFF_ALGO_VERSION,  # noqa: ARG001 — cache key only
    prior_period_label: str = "prior year",
) -> tuple[str, dict]:
    """Diff ``current_text`` against ``prior_text`` by lexical similarity.

    Each current paragraph is matched to its best prior paragraph by cosine.
    Paragraphs at or above ``dedup_cosine`` are dropped (near-duplicate); any
    material numeral change within them is captured separately.  Survivors are
    the genuine year-over-year change.

    Parameters
    ----------
    current_text, prior_text:
        Full section prose for the current and prior comparable filings.
    dedup_cosine:
        Cosine at/above which a paragraph counts as unchanged.
    numeric_delta_pct:
        Fractional figure change surfaced from deduped paragraphs.
    algo_version:
        Cache-key only (bump ``FILING_DIFF_ALGO_VERSION``).
    prior_period_label:
        Human label for the prior period (e.g. ``"FY2023"``).

    Returns
    -------
    tuple[str, dict]
        ``(rendered_text, stats)``.  ``stats`` keys: ``paragraphs_total``,
        ``paragraphs_dropped``, ``coverage_pct``, ``numeric_deltas`` (list),
        ``chars_in``, ``chars_out``.
    """
    prior_paragraphs   = _split_paragraphs(prior_text)
    current_paragraphs = _split_paragraphs(current_text)

    survivors:      list[str] = []
    numeric_deltas: list[str] = []
    dropped = 0

    for para in current_paragraphs:
        # Best prior match by cosine.
        best_prior = ""
        best_cos   = 0.0
        for prior_para in prior_paragraphs:
            cos = compute_similarity(para, prior_para).cosine
            if cos > best_cos:
                best_cos, best_prior = cos, prior_para

        if best_cos >= dedup_cosine:
            dropped += 1
            numeric_deltas.extend(
                _numeric_deltas(para, best_prior, numeric_delta_pct)
            )
        else:
            survivors.append(para)

    total = len(current_paragraphs)
    kept  = total - dropped
    coverage_pct = round(100.0 * kept / total, 1) if total else 100.0

    # --- Assemble the LLM-facing text ---
    if total and dropped == total:
        # Every paragraph deduped: the documented quiet-bullish near-verbatim
        # marker, with no body prose.
        body = (
            f"[filing-diff vs {prior_period_label}: {total} of {total} "
            f"paragraphs removed as unchanged — filing is near-verbatim]"
        )
    else:
        header = (
            f"[filing-diff vs {prior_period_label}: {dropped} of {total} "
            f"paragraphs removed as unchanged]"
        )
        body = header + "\n\n" + "\n\n".join(survivors)

    if numeric_deltas:
        body += "\n\nNUMERIC DELTAS (figures changed inside unchanged prose):\n" + \
                "\n".join(f"  - {n}" for n in numeric_deltas)

    stats: dict = {
        "paragraphs_total":   total,
        "paragraphs_dropped": dropped,
        "coverage_pct":       coverage_pct,
        "numeric_deltas":     numeric_deltas,
        "chars_in":           len(current_text),
        "chars_out":          len(body),
    }
    return body, stats
```

- [ ] **Step 5: Rewire `fetch.py`**

In `src/agents/analysts/fundamental/fetch.py`:

Replace the import block (lines 41–44):

```python
from agents.analysts.fundamental.filing_diff import (
    FILING_DIFF_ALGO_VERSION,
    filing_diff,
)
```

In `_render_diffed_section`, replace the `deboilerplate_mda(...)` call and its
zero-survivor handling (lines 346–387) with:

```python
    caps = _caps()
    try:
        filtered_text, stats = filing_diff(
            text, prior_text,
            dedup_cosine=caps.filing_dedup_cosine,
            numeric_delta_pct=caps.filing_numeric_delta_pct,
            algo_version=FILING_DIFF_ALGO_VERSION,
            prior_period_label=prior_period,
        )
        _logger.info(
            "_render_diffed_section[%s]: %s pair=%s→%s dropped=%d/%d "
            "(%.1f%% retained) deltas=%d chars %d→%d",
            section_field, form_type, prior_period, current_period,
            stats["paragraphs_dropped"], stats["paragraphs_total"],
            stats["coverage_pct"], len(stats["numeric_deltas"]),
            stats["chars_in"], stats["chars_out"],
        )
        # filing_diff already emits the documented near-verbatim marker with no
        # body when every paragraph dedups, so no zero-survivor special-case is
        # needed here (unlike the Phase 13 deboilerplate path).
        return filtered_text[:cap_chars]

    except Exception as exc:
        _logger.warning(
            "_render_diffed_section[%s]: diff failed for %s %s: %s — full text",
            section_field, form_type, current_period, exc,
        )
        return "[no prior-year pair: diff error — full text]\n\n" + text[:cap_chars]
```

(The `_caps()` import already exists in this module.)

- [ ] **Step 6: Rewire `report_cache.py`**

In `src/agents/analysts/report_cache.py`:

Replace the import (line 30):

```python
from agents.analysts.fundamental.filing_diff import FILING_DIFF_ALGO_VERSION
```

In `fundamental_hash_inputs`, replace the `deboilerplate_version` derivation and
its payload key (lines 314, 322):

```python
    filing_diff_version = FILING_DIFF_ALGO_VERSION
```

```python
        "filing_diff_version":     filing_diff_version,
```

- [ ] **Step 7: Run the full affected suites**

Run:
```bash
.venv/bin/python -m pytest \
  tests/unit/agents/analysts/fundamental/test_filing_diff.py \
  tests/unit/agents/analysts/test_report_cache_hash.py -v
```
Expected: PASS. If `test_report_cache_hash.py` asserts the old
`deboilerplate_version` key, update it to `filing_diff_version` in the same commit.

- [ ] **Step 8: Commit**

```bash
git add src/agents/analysts/fundamental/filing_diff.py src/agents/analysts/fundamental/fetch.py src/agents/analysts/report_cache.py tests/unit/agents/analysts/fundamental/test_filing_diff.py tests/unit/agents/analysts/test_report_cache_hash.py
git commit -m "feat(fundamental): filing_diff — similarity-threshold dedup + numeric deltas (renames deboilerplate)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Cache columns + `Filing` fields for persisted cosines

**Files:**
- Modify: `src/backtest/cache/schema.py` (class `FilingRow`)
- Modify: `src/data/models/filings.py` (class `Filing`)
- Modify: `src/backtest/cache/store.py` (`write_filings`)
- Test: `tests/unit/backtest/test_cache_store.py`

**Interfaces:**
- Produces: nullable `float | None` columns/fields
  `{mda,risk,litigation}_cosine_vs_prior` and `{mda,risk,litigation}_jaccard_vs_prior`.
  Read back via the existing `Filing.model_validate(row, from_attributes=True)`.
  Consumed by Tasks 5 (write) and 7 (read).

- [ ] **Step 1: Write the failing round-trip test**

Append to `tests/unit/backtest/test_cache_store.py` (`# ── filings ──` section):

```python
def test_filings_round_trip_similarity_scalars(store: CachedDataStore) -> None:
    """Persisted section cosines/Jaccards must survive write -> read.

    A column that silently dropped on write would starve the self-relative
    scale for every backtest while live kept working — the live/replay drift the
    golden cache exists to prevent.  Assert the value comes back, not merely no
    error.
    """
    filing = Filing(
        ticker="AAPL", form_type="10-Q", accession_no="0001-sim",
        filed_at=_dt(2024, 8, 1), url="https://sec/sim",
        period_of_report="2024-06-30",
        mda_cosine_vs_prior=0.87, mda_jaccard_vs_prior=0.74,
    )
    store.write_filings("AAPL", [filing])

    result = store.read_filings("AAPL", as_of=_dt(2024, 9, 1))

    assert len(result) == 1
    assert result[0].mda_cosine_vs_prior == 0.87
    assert result[0].mda_jaccard_vs_prior == 0.74
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py::test_filings_round_trip_similarity_scalars -v`
Expected: FAIL — `Filing` has no such field / column not persisted.

- [ ] **Step 3: Add the fields to `Filing`**

In `src/data/models/filings.py`, after `period_of_report`:

```python
    # Phase 14 1b — persisted self-relative similarity scalars (cosine + Jaccard
    # of each section against the prior-year comparable filing).  Computed once
    # in the fetch phase (Task 5) and read back to build the firm's own history
    # series.  Nullable: None for 8-Ks, unpaired filings, and pre-1b cache rows.
    mda_cosine_vs_prior:         float | None = None
    mda_jaccard_vs_prior:        float | None = None
    risk_cosine_vs_prior:        float | None = None
    risk_jaccard_vs_prior:       float | None = None
    litigation_cosine_vs_prior:  float | None = None
    litigation_jaccard_vs_prior: float | None = None
```

- [ ] **Step 4: Add the columns to `FilingRow`**

In `src/backtest/cache/schema.py`, inside `class FilingRow`, after
`period_of_report`:

```python
    # Phase 14 1b — persisted similarity scalars (see Filing model).  Nullable
    # Float; existing cache files self-heal via _migrate_additive_columns (a
    # nullable ALTER TABLE ADD COLUMN on store open) — NO SCHEMA_VERSION bump.
    # A refetch repopulates them; the FILING_SIMILARITY_ALGO_VERSION stamp in
    # the fundamental digest is what forces that refetch when the algo changes.
    mda_cosine_vs_prior:         float = Column(Float, nullable=True)
    mda_jaccard_vs_prior:        float = Column(Float, nullable=True)
    risk_cosine_vs_prior:        float = Column(Float, nullable=True)
    risk_jaccard_vs_prior:       float = Column(Float, nullable=True)
    litigation_cosine_vs_prior:  float = Column(Float, nullable=True)
    litigation_jaccard_vs_prior: float = Column(Float, nullable=True)
```

Ensure `Float` is imported in `schema.py` (it already is — `NewsArticleRow`
uses it).

- [ ] **Step 5: Extend the write path**

In `src/backtest/cache/store.py::write_filings`, in the
`sqlite_insert(FilingRow).values(...)` call after `period_of_report=f.period_of_report,`:

```python
                    # Phase 14 1b — persisted similarity scalars.
                    mda_cosine_vs_prior=f.mda_cosine_vs_prior,
                    mda_jaccard_vs_prior=f.mda_jaccard_vs_prior,
                    risk_cosine_vs_prior=f.risk_cosine_vs_prior,
                    risk_jaccard_vs_prior=f.risk_jaccard_vs_prior,
                    litigation_cosine_vs_prior=f.litigation_cosine_vs_prior,
                    litigation_jaccard_vs_prior=f.litigation_jaccard_vs_prior,
```

Do **not** bump `SCHEMA_VERSION`. The read path
(`Filing.model_validate(r, from_attributes=True)`) picks the columns up with no
change.

- [ ] **Step 6: Run the cache suites**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py tests/unit/backtest/test_cache_providers.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/backtest/cache/schema.py src/data/models/filings.py src/backtest/cache/store.py tests/unit/backtest/test_cache_store.py
git commit -m "feat(cache): persisted filing similarity scalars (additive, self-migrating)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Fetch-phase precompute — widen the pool, compute + persist cosines

**Files:**
- Modify: `src/data/providers/filings/edgar.py` (`_PERIODIC_BASELINE_REACH_DAYS`)
- Modify: `src/agents/analysts/fundamental/fetch_agent.py` (`_BASELINE_REACH_DAYS`)
- Modify: `src/agents/analysts/fundamental/fetch.py` (new `compute_filing_similarities`)
- Modify: `scripts/backtest_fetch` (invoke the precompute pass)
- Test: `tests/unit/agents/analysts/fundamental/test_compute_filing_similarities.py`

**Interfaces:**
- Consumes: `compute_similarity` (Task 2); `_find_prior_year_baseline` (existing
  in `fetch.py`); `FundamentalCaps.filing_history_years` (Task 1); the
  `Filing` similarity fields (Task 4).
- Produces:
  `compute_filing_similarities(filings: list[Filing]) -> list[Filing]` — returns
  copies with the six scalar fields populated where a prior-year pair exists.
  Invoked by `backtest_fetch` before `store.write_filings`.

Note: widening the pool does **not** grow LLM tokens — the pool feeds pairing /
similarity only; the prompt still receives just the current filing's diffed
sections plus the one scale-summary line (Task 7).

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the fetch-phase similarity precompute pass."""
from __future__ import annotations

from datetime import datetime

from agents.analysts.fundamental.fetch import compute_filing_similarities
from data.models import Filing


def _q(accession: str, period: str, filed: datetime, mda: str) -> Filing:
    return Filing(
        ticker="AAPL", form_type="10-Q", accession_no=accession,
        filed_at=filed, period_of_report=period, mda_excerpt=mda,
    )


def test_pairs_same_quarter_prior_year_and_populates_cosine() -> None:
    """A 10-Q must pair with the same-quarter prior-year 10-Q and get a cosine."""
    prior = _q("p", "2023-06-30", datetime(2023, 8, 1),
               "Revenue was 12.1 billion this quarter with strong demand.")
    current = _q("c", "2024-06-30", datetime(2024, 8, 1),
                 "Revenue was 13.4 billion this quarter with strong demand.")

    out = {f.accession_no: f for f in compute_filing_similarities([current, prior])}

    assert out["c"].mda_cosine_vs_prior is not None
    assert out["c"].mda_cosine_vs_prior > 0.9   # number-only change => near-verbatim
    assert out["p"].mda_cosine_vs_prior is None  # no prior pair for the oldest


def test_unpaired_filing_leaves_scalars_none() -> None:
    """A filing with no prior-year pair keeps None scalars (correct absence)."""
    lone = _q("x", "2024-06-30", datetime(2024, 8, 1), "Some MD&A prose here.")
    out = compute_filing_similarities([lone])
    assert out[0].mda_cosine_vs_prior is None
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_compute_filing_similarities.py -v`
Expected: FAIL — `compute_filing_similarities` does not exist.

- [ ] **Step 3: Implement `compute_filing_similarities` in `fetch.py`**

Add to `src/agents/analysts/fundamental/fetch.py` (imports: `compute_similarity`
from `filing_similarity`; `Filing` from `data.models`):

```python
# Section field -> (cosine field, jaccard field) on the Filing model.
_SIMILARITY_FIELDS: dict[str, tuple[str, str]] = {
    "mda_excerpt":         ("mda_cosine_vs_prior",        "mda_jaccard_vs_prior"),
    "risk_factors_excerpt":("risk_cosine_vs_prior",       "risk_jaccard_vs_prior"),
    "litigation_excerpt":  ("litigation_cosine_vs_prior", "litigation_jaccard_vs_prior"),
}


def compute_filing_similarities(filings: list[Filing]) -> list[Filing]:
    """Populate each filing's section cosine/Jaccard vs its prior-year pair.

    Fetch-phase pass (backtest: once per window; live: on filing ingestion).
    Each filing is paired with the same-form-type filing ~one fiscal year
    earlier from within ``filings`` itself, and each prose section is scored via
    the shared ``compute_similarity``.  Filings with no prior pair (the oldest
    in the pool, 8-Ks) keep ``None`` scalars — a correct absence, not a failure.

    Parameters
    ----------
    filings:
        The full pool of the ticker's filings (current window + history reach).

    Returns
    -------
    list[Filing]
        Copies with the six scalar fields set where a pair and both section
        texts exist.
    """
    pool_dicts = [f.model_dump() for f in filings]
    out: list[Filing] = []

    for filing in filings:
        updates: dict[str, float] = {}
        period = filing.period_of_report or ""

        if period:
            baseline = _find_prior_year_baseline(
                period, filing.form_type, pool_dicts,
            )
            if baseline is not None:
                for section, (cos_field, jac_field) in _SIMILARITY_FIELDS.items():
                    current_text = (getattr(filing, section) or "").strip()
                    prior_text   = (baseline.get(section) or "").strip()
                    if current_text and prior_text:
                        scores = compute_similarity(current_text, prior_text)
                        updates[cos_field] = scores.cosine
                        updates[jac_field] = scores.jaccard

        out.append(filing.model_copy(update=updates) if updates else filing)

    return out
```

- [ ] **Step 4: Widen the baseline-pool reach to `filing_history_years`**

The self-relative series needs the whole history, not ~800 days.

In `src/data/providers/filings/edgar.py`, replace the hardcoded reach:

```python
# Baseline-pool reach — derived from config so the self-relative similarity
# series (Phase 14 1b) spans the configured history horizon, not a fixed 800d.
def _baseline_reach_days() -> int:
    """Return the baseline-pool reach in days from ``filing_history_years``.

    +1 year of head-room so the OLDEST filing in the series still has its own
    prior-year pair to score against.

    Returns
    -------
    int
        Calendar days to reach back for the periodic baseline pool.
    """
    from config.analysts import get_analysts_config
    return (get_analysts_config().fundamental.filing_history_years + 1) * 366
```

Replace uses of `_PERIODIC_BASELINE_REACH_DAYS` with `_baseline_reach_days()`.

In `src/agents/analysts/fundamental/fetch_agent.py`, replace
`_BASELINE_REACH_DAYS = 800` and its use at `baseline_from = as_of - timedelta(
days=_BASELINE_REACH_DAYS)` with the same config-derived value:

```python
from config.analysts import get_analysts_config

# ... inside the fetch, replacing the constant:
                baseline_reach_days = (
                    get_analysts_config().fundamental.filing_history_years + 1
                ) * 366
                baseline_from = as_of - timedelta(days=baseline_reach_days)
```

- [ ] **Step 5: Invoke the precompute in `backtest_fetch`**

In `scripts/backtest_fetch`, immediately before the filings are written to the
store, pass them through the pass:

```python
from agents.analysts.fundamental.fetch import compute_filing_similarities

# Phase 14 1b — precompute + persist section cosines/Jaccards once, in the
# fetch phase.  The tick-time driver only reads them (cache-backed provider
# parity).  Recompute is forced by the FILING_SIMILARITY_ALGO_VERSION stamp in
# the fundamental digest, so a --refetch picks up an algo change (avoids the
# silent stale-column trap).
filings = compute_filing_similarities(filings)
store.write_filings(ticker, filings)
```

(Adapt `filings` / `ticker` / `store` to the surrounding loop's local names.)

- [ ] **Step 6: Run the tests**

Run:
```bash
.venv/bin/python -m pytest \
  tests/unit/agents/analysts/fundamental/test_compute_filing_similarities.py \
  tests/unit/data/providers/test_filings_edgar_as_of.py -v
```
Expected: PASS (precompute correct; provider as_of behaviour unchanged by the
reach widening).

- [ ] **Step 7: Commit**

```bash
git add src/data/providers/filings/edgar.py src/agents/analysts/fundamental/fetch_agent.py src/agents/analysts/fundamental/fetch.py scripts/backtest_fetch tests/unit/agents/analysts/fundamental/test_compute_filing_similarities.py
git commit -m "feat(fundamental): fetch-phase similarity precompute + config-driven history reach

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Self-relative scale summariser (`scale_summary.py`)

**Files:**
- Create: `src/agents/analysts/fundamental/scale_summary.py`
- Test: `tests/unit/agents/analysts/fundamental/test_scale_summary.py`

**Interfaces:**
- Consumes: `FundamentalCaps.filing_scale_high_pct`, `.filing_scale_low_pct`,
  `.filing_scale_min_history` (Task 1).
- Produces:
  `build_scale_summary(*, section_label: str, form_type: str,
  current_cosine: float, current_jaccard: float | None,
  history_cosines: list[float], high_pct: float, low_pct: float,
  min_history: int) -> str`. Consumed by Task 7. **Pure** — the caller does the
  as_of slicing before passing `history_cosines`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the self-relative filing-similarity scale summariser."""
from __future__ import annotations

from agents.analysts.fundamental.scale_summary import build_scale_summary


def _summary(current: float, history: list[float]) -> str:
    return build_scale_summary(
        section_label="MD&A", form_type="10-Q",
        current_cosine=current, current_jaccard=0.7,
        history_cosines=history,
        high_pct=0.80, low_pct=0.20, min_history=3,
    )


def test_changed_more_than_usual_is_flagged_bottom() -> None:
    """A cosine below the firm's own history reads as 'changed more than usual'."""
    text = _summary(0.50, [0.90, 0.92, 0.88, 0.91, 0.89])
    assert "more than usual" in text
    assert "10-Q MD&A" in text


def test_changed_less_than_usual_is_flagged_top() -> None:
    """A cosine above the firm's own history reads as 'changed less than usual'."""
    text = _summary(0.97, [0.70, 0.72, 0.68, 0.75, 0.71])
    assert "less than usual" in text


def test_thin_history_hedges_instead_of_banding() -> None:
    """With too few prior points, hedge honestly — no false-precision band."""
    text = _summary(0.80, [0.75])
    assert "limited" in text.lower() or "only" in text.lower()
    assert "percentile" not in text.lower()
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_scale_summary.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Implement `scale_summary.py`**

```python
"""Self-relative filing-similarity scale summariser.

Turns a firm's current section cosine + its OWN prior cosine history into a
single deterministic sentence for the LLM — magnitude context only, deliberately
sign-free (direction comes from the diff content).  Each firm is its own
baseline, which sidesteps sector heterogeneity without any point-in-time wrinkle
(all history is already as_of-sliced by the caller).

The chronic-changer blind spot (a habitual churner looks "normal for it") is
accepted for v1: the diff still surfaces the actual changes for the LLM to
judge, so the scale never launders them into neutral.
"""
from __future__ import annotations


def _percentile(value: float, series: list[float]) -> float:
    """Return the fraction of ``series`` strictly below ``value`` (0.0–1.0).

    Parameters
    ----------
    value:
        The current cosine.
    series:
        The firm's own prior cosines (non-empty).

    Returns
    -------
    float
        Rank fraction in [0.0, 1.0].
    """
    below = sum(1 for h in series if h < value)
    return below / len(series)


def build_scale_summary(
    *,
    section_label: str,
    form_type: str,
    current_cosine: float,
    current_jaccard: float | None,
    history_cosines: list[float],
    high_pct: float,
    low_pct: float,
    min_history: int,
) -> str:
    """Build one self-relative scale sentence for the LLM context.

    Parameters
    ----------
    section_label:
        Human section name (e.g. ``"MD&A"``).
    form_type:
        Filing form type (e.g. ``"10-Q"``) — the series is form-type-specific.
    current_cosine:
        This filing's section cosine vs its prior-year pair.
    current_jaccard:
        Secondary measure, reported as a raw number if present.
    history_cosines:
        The firm's OWN prior cosines for this section + form type, already
        as_of-sliced by the caller.  May be empty.
    high_pct, low_pct:
        Percentile band cut-offs.
    min_history:
        Minimum prior points before a percentile is trustworthy.

    Returns
    -------
    str
        A single line, e.g. ``"MD&A similarity vs prior 10-Q: cosine 0.71,
        jaccard 0.63 — 12th percentile of this firm's own 10-Q history (n=11):
        changed MORE than usual for this firm."``
    """
    jac = f", jaccard {current_jaccard:.2f}" if current_jaccard is not None else ""
    head = (
        f"{section_label} similarity vs prior {form_type}: "
        f"cosine {current_cosine:.2f}{jac}"
    )

    # Thin history — hedge rather than fabricate a percentile.
    if len(history_cosines) < min_history:
        n = len(history_cosines)
        return (
            f"{head} — only {n} prior {form_type} "
            f"{'point' if n == 1 else 'points'} for this firm; "
            f"limited baseline, judge the change from the diff content."
        )

    pct = _percentile(current_cosine, history_cosines)
    n = len(history_cosines)

    if pct <= low_pct:
        band = "changed MORE than usual for this firm"
    elif pct >= high_pct:
        band = "changed LESS than usual for this firm"
    else:
        band = "typical amount of change for this firm"

    return (
        f"{head} — {round(pct * 100)}th percentile of this firm's own "
        f"{form_type} {section_label} history (n={n}): {band}."
    )
```

- [ ] **Step 4: Run to see it pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_scale_summary.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/scale_summary.py tests/unit/agents/analysts/fundamental/test_scale_summary.py
git commit -m "feat(fundamental): self-relative similarity scale summariser

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: Wire the scale summary into the LLM context

**Files:**
- Modify: `src/agents/analysts/fundamental/fetch.py` (`_render_diffed_section` /
  `_build_ticker_context`)
- Test: `tests/unit/agents/analysts/fundamental/test_build_ticker_context.py`

**Interfaces:**
- Consumes: `build_scale_summary` (Task 6); the persisted `Filing`/dict cosine
  fields (Tasks 4–5); `_SIMILARITY_FIELDS` (Task 5); `FundamentalCaps` scale
  settings (Task 1); the widened `baseline_filings_payload` as the as_of-sliced
  history source (Task 5 — the provider already `filed_at <= as_of` filters it).
- Produces: a `"   scale: <summary>"` line beneath each diffed section in the
  context block.

- [ ] **Step 1: Write the failing test**

```python
def test_context_carries_self_relative_scale_line() -> None:
    """The MD&A section must carry a self-relative scale line built from the
    firm's own persisted cosine history in the baseline pool."""
    from agents.analysts.fundamental.fetch import _build_ticker_context
    from data.models import Form4Bundle

    current = {
        "form_type": "10-Q", "accession_no": "c", "filed_at": "2024-08-01",
        "period_of_report": "2024-06-30",
        "mda_excerpt": "A newly disclosed regulatory probe may materially harm results. " * 20,
        "mda_cosine_vs_prior": 0.40,
    }
    # Four years of prior-year cosines for this firm's 10-Q MD&A.
    history = [
        {"form_type": "10-Q", "accession_no": f"h{i}", "period_of_report": p,
         "mda_excerpt": "boilerplate " * 80, "mda_cosine_vs_prior": c}
        for i, (p, c) in enumerate(
            [("2020-06-30", 0.90), ("2021-06-30", 0.92),
             ("2022-06-30", 0.88), ("2023-06-30", 0.91)]
        )
    ]

    block = _build_ticker_context(
        "AAPL", [current], Form4Bundle(trades=[], derivatives=[]),
        insider_lookback_days=30, ratios=None,
        baseline_filings_payload=history,
    )
    assert "scale:" in block
    assert "more than usual" in block   # cosine 0.40 sits below the 0.88-0.92 history
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_build_ticker_context.py::test_context_carries_self_relative_scale_line -v`
Expected: FAIL — no scale line emitted.

- [ ] **Step 3: Build the history series + scale line**

In `src/agents/analysts/fundamental/fetch.py`, add a helper and call it from
`_render_diffed_section` (which already receives `filing`, `section_field`,
`baselines`):

```python
def _scale_line(
    filing: dict,
    section_field: str,
    baselines: list[dict],
) -> str | None:
    """Return the self-relative scale line for a section, or None.

    Builds the firm's OWN prior-cosine series for this section + form type from
    the (as_of-sliced) baseline pool, and bands the current filing's persisted
    cosine against it via ``build_scale_summary``.  Returns None when the
    current filing carries no persisted cosine (unpaired / 8-K).

    Parameters
    ----------
    filing:
        Current filing dict (carries the persisted ``*_cosine_vs_prior`` fields).
    section_field:
        One of the keys in ``_SIMILARITY_FIELDS``.
    baselines:
        The widened baseline pool (already ``filed_at <= as_of``).

    Returns
    -------
    str | None
        The scale summary sentence, or None if no current cosine exists.
    """
    cos_field, jac_field = _SIMILARITY_FIELDS[section_field]
    current_cosine = filing.get(cos_field)
    if current_cosine is None:
        return None

    form_type = filing.get("form_type", "?")

    # The firm's own prior-cosine series: same section + form type, excluding
    # the current filing, only where a cosine was persisted.
    history = [
        b[cos_field]
        for b in baselines
        if b.get("form_type") == form_type
        and b.get("accession_no") != filing.get("accession_no")
        and b.get(cos_field) is not None
    ]

    caps = _caps()
    section_label = {
        "mda_excerpt": "MD&A",
        "risk_factors_excerpt": "Risk factors",
        "litigation_excerpt": "Litigation",
    }[section_field]

    return build_scale_summary(
        section_label=section_label,
        form_type=form_type,
        current_cosine=current_cosine,
        current_jaccard=filing.get(jac_field),
        history_cosines=history,
        high_pct=caps.filing_scale_high_pct,
        low_pct=caps.filing_scale_low_pct,
        min_history=caps.filing_scale_min_history,
    )
```

At the end of `_render_diffed_section`, after computing the diffed body but
before returning, append the scale line when present. Change the two successful
`return` sites to build a combined string:

```python
    scale = _scale_line(filing, section_field, baselines)
    tail = f"\n   scale: {scale}" if scale else ""
    return filtered_text[:cap_chars] + tail
```

(Import `build_scale_summary` from `scale_summary` at the top of `fetch.py`.)

- [ ] **Step 4: Run to see it pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_build_ticker_context.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agents/analysts/fundamental/fetch.py tests/unit/agents/analysts/fundamental/test_build_ticker_context.py
git commit -m "feat(fundamental): inject self-relative similarity scale into LLM context

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 8: Fundamental prompt rework — magnitude from scale, direction from diff

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py`
- Test: `tests/unit/agents/analysts/fundamental/test_fundamental_prompt.py`

**Interfaces:**
- Consumes: nothing new at runtime — this is prompt copy that describes the
  Task 3 markers (`[filing-diff ...]`, `NUMERIC DELTAS`) and the Task 7
  `scale:` line.

- [ ] **Step 1: Write the failing test**

```python
def test_prompt_describes_scale_and_diff_direction() -> None:
    """The rendered prompt must teach magnitude-from-scale, direction-from-diff,
    and must NOT carry the stale volume heuristic."""
    from agents.analysts.heuristics import load_heuristics
    from agents.analysts.fundamental.prompts import build_fundamental_instruction

    instr = build_fundamental_instruction(load_heuristics().fundamental_vocabulary)

    assert "scale:" in instr
    assert "NUMERIC DELTAS" in instr
    # The old survival=rewrite heuristic must be gone (survival no longer proxies
    # rewriting under similarity dedup).
    assert "Heavy survival across sections" not in instr
    assert "heavily rewritten filing = stronger bearish prior" not in instr
```

- [ ] **Step 2: Run to see it fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_fundamental_prompt.py::test_prompt_describes_scale_and_diff_direction -v`
Expected: FAIL — scale/NUMERIC-DELTA copy absent; volume heuristic still present.

- [ ] **Step 3: Edit the prompt template**

In `src/agents/analysts/fundamental/prompts.py`, `_TEMPLATE`:

(a) In the `-- COMPANY FILINGS (PROSE) --` description, after the marker
semantics block, add:

```
    SELF-RELATIVE SCALE — a "scale:" line beneath each diffed section reports
    how similar THIS filing is to the SAME firm's prior comparable filing
    (cosine), and where that sits in the firm's OWN history ("changed MORE /
    LESS than usual for this firm").  Use the scale for MAGNITUDE: a filing that
    changed far more than this firm usually does warrants a larger move than one
    whose change is typical.  It is deliberately SIGN-FREE — take DIRECTION from
    the diff CONTENT and the NUMERIC DELTAS, not from the scale.

    NUMERIC DELTAS — a "NUMERIC DELTAS" block lists figures that changed inside
    otherwise-unchanged prose (e.g. a contingency 1.0 -> 3.0 bn).  These are
    real changes the language-similarity view intentionally normalises away;
    weigh them on their merits.
```

(b) Replace the VOLUME heuristic (the three lines beginning
"Weigh the VOLUME of change too" through "= stronger bearish prior.") with:

```
   Weigh the SELF-RELATIVE SCALE for sizing: the "scale:" line tells you how
   this filing's change compares to the firm's own norm.  Do NOT infer "heavily
   rewritten" from how much text survived the diff — survival is now a
   similarity threshold, not a volume count.  A firm that changed far more than
   usual (bottom of its own history) plus a bearish diff direction is a
   high-magnitude bearish read; typical-or-less change tempers magnitude.
```

(c) Leave the sign-convention repetition intact (deliberate reinforcement of
the traded edge).

- [ ] **Step 4: Run to see it pass**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_fundamental_prompt.py -v`
Expected: PASS.

- [ ] **Step 5: Full unit sweep (the prompt digest feeds the report cache)**

Run: `.venv/bin/python -m pytest tests/unit -q`
Expected: PASS. `FUNDAMENTAL_PROMPT_VERSION` auto-rederives from the new copy —
no manual bump needed.

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/prompts.py tests/unit/agents/analysts/fundamental/test_fundamental_prompt.py
git commit -m "feat(fundamental): prompt — magnitude from self-relative scale, direction from diff

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 9: Refetch, re-run the spot-check, verify de-skew (operator steps)

**Files:** none (operator + verification).

The `FILING_SIMILARITY_ALGO_VERSION` / `FILING_DIFF_ALGO_VERSION` stamps in the
fundamental digest mean the persisted cosines and the report cache are both
stale until a refetch runs — so a plain re-run would serve old verdicts. Refetch
first.

- [ ] **Step 1: Refetch the spot-check window (repopulates cosines + widened pool)**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch \
  --window long-baseline-2025 --refetch-domain filings
```
Expected: log lines showing the widened baseline pool
(`filing_history_years + 1` years) and non-error completion. Confirm the
persisted cosines populated:
```bash
.venv/bin/python -c "import sqlite3, glob; db=sorted(glob.glob('backtests/long-baseline-2025/**/cache*.sqlite', recursive=True))[0]; c=sqlite3.connect(db); print(c.execute('select count(*) from filings where mda_cosine_vs_prior is not null').fetchone())"
```
Expected: a non-zero count (paired 10-Q/10-K filings now carry a cosine).

- [ ] **Step 2: Re-run the 2 spot-check ticks**

Run:
```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_run \
  --window long-baseline-2025 --max-ticks 2
```

- [ ] **Step 3: Verify the lean distribution de-skews (the gate)**

Inspect the fundamental verdicts across the 20 tickers in the run's traces.
**Pass criteria:**
- At least one **bullish** fundamental verdict appears (the leg Plan 1 could
  never produce).
- The number-stable names (whose filings only rolled figures forward) no longer
  read as "heavily rewritten" — their `scale:` line shows a high percentile
  ("changed LESS than usual") and their lean is not strongly bearish.
- Magnitudes are spread, not clustered at 0.6–0.75.

If the distribution is still degenerate, STOP and diagnose before the full eval
(do not proceed to Plans 4–5). If it de-skews, the eval is unblocked.

- [ ] **Step 4: Record the outcome**

Append a short note to `docs/Phase14-analyst-refactor/` (lean distribution
before vs after, a representative de-skewed ticker) so the eval has its baseline.

---

## Self-review

**Spec coverage** (against the converged design):
1. Shared lexical primitive (cosine + Jaccard, number-normalised) → Task 2. ✓
2. `filing_diff` rename + similarity dedup + numeric deltas → Task 3. ✓
3. Persisted per-filing cosines (precompute, not tick-time) → Tasks 4–5. ✓
4. Self-relative scale summariser (per form-type series, thin-history hedge) →
   Task 6, wired in Task 7. ✓
5. Two series per firm, never pooled (form-type filter in `_scale_line`) →
   Task 7. ✓
6. Direction from diff content, magnitude from scale (chronic-changer
   mitigation) → Task 8 prompt. ✓
7. PIT (baseline pool is as_of-sliced), parity (one shared function), algo
   stamps (bust cache + force refetch) → Global Constraints + Tasks 3/5/9. ✓
8. Config-driven thresholds + history horizon → Task 1. ✓

**Deferred (backlog, not built here):** sector-corpus absolute percentile
(closes the chronic-changer blind spot); neural-embedding similarity;
drop-old-bodies storage reclaim (the persist-scalars groundwork is laid in
Task 4); the live-side on-ingestion trigger (pre-deployment — no instance).

**Type consistency:** `compute_similarity -> SimilarityScores(cosine, jaccard)`
used identically in Tasks 3/5/6; `filing_diff(...) -> (text, stats)` with
`stats["numeric_deltas"]` used in Tasks 3/(fetch); the six
`{section}_cosine/jaccard_vs_prior` field names identical across Tasks 4/5/7;
`build_scale_summary(**kwargs) -> str` signature identical in Tasks 6/7.

**Open v1 tuning knobs** (config, revisit after the de-skew): `filing_dedup_cosine`
0.92, `filing_history_years` 7, scale bands 0.20/0.80 — all toggleable without
code change.
