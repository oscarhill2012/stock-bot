# Plan 1 — Filing-Delta Fundamental Signal ("Lazy Prices") Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the fundamental analyst around the Cohen–Malloy–Nguyen "Lazy Prices" effect — diff each 10-K/10-Q against its previous comparable filing (MD&A, risk factors, litigation), treat substantive change as bearish by default and genuine absence of change as quiet-bullish, and emit a long `horizon_days` (config-driven, 60) so downstream consumers know the signal operates over 3–6 months.

**Architecture:** The prior-year pairing machinery (Phase 13) already retrieves the previous comparable filing on both the live EDGAR path and the golden-cache path — this plan extends it rather than rebuilding it. Three extensions: (1) a new `litigation_excerpt` section flows from EDGAR through the `Filing` model and the cache schema (additive nullable column, self-healing migration); (2) the paragraph-diff render that today covers only MD&A is generalised to risk factors and litigation; (3) the prompt is rewritten diff-first with the Lazy Prices sign convention, and the LLM emit schema gains a required `horizon_days` field. The verdict flows through the existing `fundamental` analyst stream unchanged.

**Tech Stack:** Python 3.14, Pydantic v2, Google ADK, edgartools (EDGAR), SQLAlchemy + SQLite (golden cache), pytest.

## Global Constraints

- **British English everywhere** — code identifiers, comments, docs, prose (`behaviour`, `normalise`, `analyse`).
- **Comment-heavy code** — every function gets a docstring (purpose, parameters, return value); non-trivial logic gets inline comments; blank lines between logical blocks.
- **Config convention** — every tunable lives in `config/*.json`; each addition updates `config/README.md` in the same task. Never hardcode.
- **Loud failures** — prefer raises over silent null/empty/neutral degradation; tests assert positive signals (the diff fired, the pair was selected), not merely absence of errors.
- **Backtest PIT rules** — every read of `state["as_of"]` goes through `resolve_as_of`; any datetime written to ADK state is ISO-stringified first. (No task in this plan touches ADK state directly, but Task 4 modifies code adjacent to it — do not regress this.)
- **Shell conventions** — never prefix Bash commands with `cd`; run from the project root. Tests: `.venv/bin/python -m pytest tests/... -v`. Scripts: `PYTHONPATH=src .venv/bin/python -m scripts.<name>`.
- **`.git/info/exclude` gotcha** — new files under `tests/unit/data/` are silently ignored by a bare `data` pattern; `git add -f` them (called out in the relevant commit step).
- **Horizon contract ownership:** Task 5 of this plan adds `horizon_days` to **both** `AnalystVerdict` (canonical, `Field(default=1, ge=1)`, inherited by `TickerVerdict`) and `LlmTickerVerdict` (emit schema, required — no default) in `src/contract/evidence.py`. This is the shared drift-horizon contract the whole Phase 14 programme builds on: Plan 3's news rebuild and Plans 4–5's macro/linkage analyst all **consume** it. Task 5 also interim-patches the current news prompt so it stays schema-valid until Plan 3 replaces that prompt wholesale. Plan 1 therefore runs **first** in the programme; do **not** add defensive `getattr` shims for the field anywhere.

## Context primer (read once before Task 1)

| Concern | Where it lives today |
| --- | --- |
| Previous-comparable pairing (335–395-day window, same form type, matched on `period_of_report`) | `src/agents/analysts/fundamental/fetch.py::_find_prior_year_baseline` |
| Paragraph-level SHA-256 diff (generic over any prose) | `src/agents/analysts/fundamental/deboilerplate.py::deboilerplate_mda` |
| MD&A-only diff render + stub guard + fallback markers | `src/agents/analysts/fundamental/fetch.py::_render_mda` (replaced in Task 4) |
| Prior-year *pool* retrieval, live (backfill mode, 800-day reach) | `src/data/providers/filings/edgar.py::fetch` (`from_date` given) |
| Prior-year *pool* retrieval, backtest (pool mode) | `src/backtest/providers/filings_cache.py::fetch` (`from_date` given) |
| Pool request per tick (both paths, provider-dispatched) | `src/agents/analysts/fundamental/fetch_agent.py` (`baseline_filings`) |
| Cache schema + self-healing additive migration | `src/backtest/cache/schema.py::FilingRow`, `src/backtest/cache/store.py::_migrate_additive_columns` |
| Refetch of an existing window's domain | `scripts/backtest_fetch.py --refetch-domain filings` (clears rows, re-writes) |
| LLM emit schema → canonical verdict | `src/contract/evidence.py::LlmTickerVerdict.to_ticker_verdict` |
| Prompt-version cache busting | `FUNDAMENTAL_PROMPT_VERSION` in `src/agents/analysts/report_cache.py` is **auto-derived** from the rendered prompt — no manual bump needed when Task 6 rewrites the template. |

The XOM gotcha this plan must handle: XOM's 10-K Item 7 is a cross-reference stub ("incorporated by reference to Exhibit 13"), so its 10-K MD&A never clears the stub threshold and the diff signal must come from the 10-Q sections instead. The stub guard already exists; Task 4 pins the behaviour with a test and Task 6 teaches the prompt that a no-comparison marker is **not** evidence of "no change".

---

### Task 1: Config — litigation cap, risk-cap raise, filing-delta horizon

**Files:**
- Modify: `src/config/analysts.py` (class `FundamentalCaps`, ~line 182)
- Modify: `config/analysts.json` (`fundamental` block)
- Modify: `config/README.md` (`fundamental` caps table, ~line 237)
- Test: `tests/unit/config/test_analysts_config.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `FundamentalCaps.max_filing_litigation_chars: int` (value 1500) and `FundamentalCaps.filing_delta_horizon_days: int` (value 60) — read by Task 4 (render cap) and Task 6 (prompt substitution). Also raises `max_filing_risk_chars` 1500 → 4000 (risk factors are diffed from Task 4 onward, so the cap now bounds *survivors*, mirroring the Phase 13 MD&A raise).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/config/test_analysts_config.py`:

```python
# ---------------------------------------------------------------------------
# Phase 14 Plan 1 — filing-delta settings
# ---------------------------------------------------------------------------

def test_fundamental_litigation_cap_loaded() -> None:
    """``max_filing_litigation_chars`` must load from config/analysts.json.

    The litigation section (Legal Proceedings) joins MD&A and risk factors as
    a diffed prose block in Phase 14; its render cap must be config-driven,
    never hardcoded in the assembly layer.
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.max_filing_litigation_chars == 1500


def test_fundamental_filing_delta_horizon_loaded() -> None:
    """``filing_delta_horizon_days`` must load from config/analysts.json.

    This is the trading-day horizon the fundamental prompt instructs the LLM
    to emit as ``horizon_days`` — the Lazy Prices drift operates at 3–6
    months, so the default is 60 trading days.
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.filing_delta_horizon_days == 60


def test_fundamental_risk_cap_raised_for_diffed_survivors() -> None:
    """Risk-factor cap is 4000 now that unchanged paragraphs are stripped first.

    Pre-Phase 14 the cap bounded the raw (mostly boilerplate) section at 1500;
    post-diff it bounds only the year-over-year survivors, so it is raised —
    the same reasoning as the Phase 13 MD&A raise (1500 → 12000).
    """
    from config.analysts import load_analysts_config

    cfg = load_analysts_config()

    assert cfg.fundamental.max_filing_risk_chars == 4000
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v -k "litigation_cap or filing_delta_horizon or risk_cap_raised"`
Expected: 3 FAILED — `AttributeError`/`ValidationError` for the two new fields, assertion `1500 != 4000` for the risk cap.

- [ ] **Step 3: Add the two fields to `FundamentalCaps`**

In `src/config/analysts.py`, inside `class FundamentalCaps`, directly after the `mda_stub_char_threshold` field declaration, add:

```python
    # Phase 14 (Lazy Prices) — character cap on the Legal Proceedings
    # (litigation) excerpt rendered per periodic filing.  Applied after the
    # prior-year paragraph diff, so it bounds the year-over-year survivors.
    max_filing_litigation_chars: int = Field(ge=1, le=20_000, default=1_500)

    # Phase 14 (Lazy Prices) — trading-day horizon the fundamental prompt
    # instructs the LLM to emit as ``horizon_days``.  The filing-language
    # drift documented by Cohen, Malloy & Nguyen (2020) operates over 3–6
    # months; 60 trading days sits inside that window.  Bounded at one
    # trading year.
    filing_delta_horizon_days: int = Field(ge=1, le=250, default=60)
```

Also extend the class docstring's `Attributes` section with two entries:

```
    max_filing_litigation_chars:
        Maximum characters of Legal Proceedings (litigation) text included
        per periodic filing, applied after prior-year diffing (Phase 14).
    filing_delta_horizon_days:
        Trading-day horizon the prompt instructs the LLM to emit as
        ``horizon_days`` — the Lazy Prices drift window (Phase 14).
```

And update the `max_filing_risk_chars` docstring line from "No de-boilerplate pass is applied to risk factors." to:

```
    max_filing_risk_chars:
        Maximum characters of risk-factor text included per filing.  Applied
        after prior-year paragraph diffing (Phase 14) — bounds the survivors,
        not the raw section.
```

- [ ] **Step 4: Update `config/analysts.json`**

In the `"fundamental"` block, change `"max_filing_risk_chars"` and add the two new keys (keep the existing key order style):

```json
    "max_filing_mda_chars":       12000,
    "max_filing_risk_chars":      4000,
    "max_filing_litigation_chars": 1500,
    "max_filing_8k_body_chars":   1500,
    "max_insider_footnotes":      5,
    "max_insider_footnote_chars": 400,
    "mda_stub_char_threshold":    400,
    "filing_delta_horizon_days":  60,
```

(`insider_conviction_threshold_dollars`, `trailing_pe_implausibility_threshold` and the `llm` block are untouched.)

- [ ] **Step 5: Update `config/README.md`**

In the `### fundamental — Fundamental analyst input caps` table:

- Change the `fundamental.max_filing_risk_chars` row description to: `Character cap on the risk-factors excerpt for each filing. Applied **after** the Phase 14 prior-year paragraph diff (unchanged paragraphs stripped first, survivors capped here). Raised from 1500 → 4000 alongside the diff, mirroring the Phase 13 MD&A raise. Default 4000.`
- Add a row: `| fundamental.max_filing_litigation_chars | int [1–20000] | Character cap on the Legal Proceedings (litigation) excerpt rendered per periodic filing (Phase 14 "Lazy Prices"). Applied after prior-year diffing. Default 1500. |`
- Add a row: `| fundamental.filing_delta_horizon_days | int [1–250] | Trading-day horizon the fundamental prompt instructs the LLM to emit as horizon_days. The filing-delta drift (Cohen, Malloy & Nguyen 2020) operates over 3–6 months; default **60**. |`
- Also update the `fundamental.mda_stub_char_threshold` row description to note it now gates **all three** diffed sections (MD&A, risk factors, litigation), not MD&A alone.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/config/test_analysts_config.py -v`
Expected: all PASS (new and pre-existing).

- [ ] **Step 7: Commit**

```bash
git add src/config/analysts.py config/analysts.json config/README.md tests/unit/config/test_analysts_config.py
git commit -m "feat(config): litigation cap, risk-cap raise, filing-delta horizon (Phase 14 Plan 1)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Filing model + EDGAR litigation section fetch

**Files:**
- Modify: `src/data/models/filings.py` (class `Filing`)
- Modify: `src/data/providers/filings/edgar.py` (`_SECTION_KEYS`, `_build_filing`)
- Test: `tests/unit/data/providers/test_filings_edgar_as_of.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Filing.litigation_excerpt: str | None` (default `None`) — consumed by Task 3 (cache column), Task 4 (render). EDGAR section keys: 10-K → `part_i_item_3`, 10-Q → `part_ii_item_1` (verified live in Step 5).

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/data/providers/test_filings_edgar_as_of.py` (reuse the existing `_FakeFiling` / `_patch_seams` helpers already defined in the file):

```python
# ---------------------------------------------------------------------------
# Litigation section extraction (Phase 14 Plan 1)
# ---------------------------------------------------------------------------

class _FakeSection:
    """Stand-in for an edgartools section object exposing ``.text()``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def text(self) -> str:
        """Return the raw section text."""
        return self._text


class _FakeSections:
    """Stand-in for edgartools' sections container exposing ``.get(key)``."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get(self, key: str):
        """Return a ``_FakeSection`` for a known key, else ``None``."""
        text = self._mapping.get(key)
        return _FakeSection(text) if text is not None else None


class _FakeSectionedFiling(_FakeFiling):
    """A fake 10-K/10-Q whose ``.obj()`` exposes named sections."""

    def __init__(self, *, form: str, sections: dict[str, str], **kwargs) -> None:
        super().__init__(form=form, **kwargs)
        self._sections = sections

    def obj(self):
        """Return an object carrying a ``sections`` container."""

        class _Obj:
            sections = _FakeSections(self._sections)

        return _Obj()


def test_build_filing_extracts_10k_litigation_section() -> None:
    """A 10-K's Legal Proceedings section (part_i_item_3) must populate
    ``litigation_excerpt`` — the Phase 14 filing-delta signal diffs litigation
    language year-over-year, so a silent None here starves the whole channel.
    """
    import data.providers.filings.edgar as mod

    fake = _FakeSectionedFiling(
        form="10-K",
        filing_date=date(2026, 1, 30),
        accession_no="lit-10k",
        sections={
            "part_i_item_1a": "Risk factors text.",
            "part_ii_item_7": "MD&A text.",
            "part_i_item_3":  "In re Example Securities Litigation, filed 2025.",
        },
    )

    built = mod._build_filing(fake, "AAPL", include_excerpts=True)

    # Positive signal: the litigation prose arrived, alongside the two
    # pre-existing sections (no regression).
    assert built.litigation_excerpt == "In re Example Securities Litigation, filed 2025."
    assert built.mda_excerpt == "MD&A text."
    assert built.risk_factors_excerpt == "Risk factors text."


def test_build_filing_extracts_10q_litigation_section() -> None:
    """A 10-Q's Legal Proceedings section (part_ii_item_1) must populate
    ``litigation_excerpt`` — note 10-Q risk factors live at part_ii_item_1a,
    so the two keys must not be conflated.
    """
    import data.providers.filings.edgar as mod

    fake = _FakeSectionedFiling(
        form="10-Q",
        filing_date=date(2026, 2, 10),
        accession_no="lit-10q",
        sections={
            "part_ii_item_1a": "Quarterly risk factors.",
            "part_i_item_2":   "Quarterly MD&A.",
            "part_ii_item_1":  "The company is a defendant in ongoing patent litigation.",
        },
    )

    built = mod._build_filing(fake, "AAPL", include_excerpts=True)

    assert built.litigation_excerpt == "The company is a defendant in ongoing patent litigation."
    assert built.risk_factors_excerpt == "Quarterly risk factors."


def test_build_filing_litigation_none_when_section_absent() -> None:
    """A filing without a Legal Proceedings section yields ``None`` (nullable
    field) — the render layer treats absence as 'section not filed', never as
    an empty diff.
    """
    import data.providers.filings.edgar as mod

    fake = _FakeSectionedFiling(
        form="10-K",
        filing_date=date(2026, 1, 30),
        accession_no="lit-none",
        sections={
            "part_i_item_1a": "Risk factors only.",
            "part_ii_item_7": "MD&A only.",
        },
    )

    built = mod._build_filing(fake, "AAPL", include_excerpts=True)

    assert built.litigation_excerpt is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/data/providers/test_filings_edgar_as_of.py -v -k litigation`
Expected: FAIL — `AttributeError: 'Filing' object has no attribute 'litigation_excerpt'` (or `TypeError` on the fake's constructor if run order differs; either way, red).

- [ ] **Step 3: Add the field to the `Filing` model**

In `src/data/models/filings.py`, after the `mda_excerpt` field, add:

```python
    litigation_excerpt: str | None = Field(
        default=None,
        description=(
            "Full text of the Legal Proceedings section when available "
            "(10-K Part I Item 3; 10-Q Part II Item 1).  No truncation at "
            "fetch time — the assembly layer applies prior-year diffing then "
            "caps the rendered output via max_filing_litigation_chars.  "
            "None for form types without the section (8-K) and for cache "
            "rows written before Phase 14."
        ),
    )
```

Also append to the class docstring:

```
    Phase 14 additions (filing-delta / Lazy Prices):
    - ``litigation_excerpt`` — Legal Proceedings prose, diffed year-over-year
      by the assembly layer alongside MD&A and risk factors.
```

- [ ] **Step 4: Wire the section keys into the EDGAR provider**

In `src/data/providers/filings/edgar.py`:

1. Extend `_SECTION_KEYS`:

```python
# Section keys per edgartools naming. 8-Ks have no stable RF/MD&A so
# they're skipped — we still return the metadata.
# Phase 14: Legal Proceedings joins the extracted set (10-K Part I Item 3,
# 10-Q Part II Item 1) so the filing-delta prompt can diff litigation
# language year-over-year.  NB the 10-Q keys are close but distinct:
# part_ii_item_1 is Legal Proceedings, part_ii_item_1a is Risk Factors.
_SECTION_KEYS = {
    "10-K": {
        "risk_factors_excerpt": "part_i_item_1a",
        "mda_excerpt":          "part_ii_item_7",
        "litigation_excerpt":   "part_i_item_3",
    },
    "10-Q": {
        "risk_factors_excerpt": "part_ii_item_1a",
        "mda_excerpt":          "part_i_item_2",
        "litigation_excerpt":   "part_ii_item_1",
    },
}
```

2. In `_build_filing`, replace the excerpt-extraction block:

```python
    risk: str | None = None
    mda: str | None = None
    litigation: str | None = None

    if include_excerpts and form_type in _SECTION_KEYS:
        try:
            obj = filing.obj()
            keys = _SECTION_KEYS[form_type]

            # Each section is independently nullable — a filing may carry
            # MD&A but no Legal Proceedings (and vice versa).
            risk       = _section_text(obj, keys["risk_factors_excerpt"])
            mda        = _section_text(obj, keys["mda_excerpt"])
            litigation = _section_text(obj, keys["litigation_excerpt"])
        except Exception:
            risk = None
            mda = None
            litigation = None
```

3. In the trailing `Filing(...)` construction of `_build_filing`, add `litigation_excerpt=litigation,` immediately after `mda_excerpt=mda,`.

- [ ] **Step 5: Verify the edgartools section-key names against live EDGAR**

The two litigation keys follow edgartools' existing naming convention (`part_i_item_1a`, `part_ii_item_7` are already in production use), but `_section_text` returns `None` for an unknown key — a wrong key name would fail *silently*. Verify once against real filings (requires `EDGAR_IDENTITY` in the environment/secrets):

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
"""One-off check: confirm edgartools section keys for Legal Proceedings."""
from edgar import Company, set_identity
from data.secrets import require_key

set_identity(require_key("EDGAR_IDENTITY"))

for form, expected in (("10-K", "part_i_item_3"), ("10-Q", "part_ii_item_1")):
    filing = list(Company("AAPL").get_filings(form=form, amendments=False).head(1))[0]
    obj = filing.obj()
    section = obj.sections.get(expected)
    text = section.text() if section is not None and hasattr(section, "text") else None
    status = "OK" if text and text.strip() else "MISSING"
    print(f"{form}: {expected} -> {status}")
    if status == "MISSING":
        # Surface whatever the container exposes so the correct key can be read off.
        print("   available sections object:", obj.sections)
PY
```

Expected output: `10-K: part_i_item_3 -> OK` and `10-Q: part_ii_item_1 -> OK`. **If either prints MISSING**, read the correct key name from the printed container and update `_SECTION_KEYS` (and the two unit tests' fixture keys) to match — then re-run this snippet until both print OK. Do not proceed with a MISSING key.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/data/providers/test_filings_edgar_as_of.py -v`
Expected: all PASS (new litigation tests plus the full pre-existing file — the provider change must not disturb 8-K/amendment/selection behaviour).

- [ ] **Step 7: Commit**

Note the forced add — `.git/info/exclude` has a bare `data` pattern that silently ignores paths under `tests/unit/data/`:

```bash
git add src/data/models/filings.py src/data/providers/filings/edgar.py
git add -f tests/unit/data/providers/test_filings_edgar_as_of.py
git commit -m "feat(filings): fetch Legal Proceedings section into Filing.litigation_excerpt

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Golden-cache schema + store write for `litigation_excerpt`

**Files:**
- Modify: `src/backtest/cache/schema.py` (class `FilingRow`)
- Modify: `src/backtest/cache/store.py` (`write_filings`)
- Test: `tests/unit/backtest/test_cache_store.py`

**Interfaces:**
- Consumes: `Filing.litigation_excerpt` (Task 2).
- Produces: `FilingRow.litigation_excerpt` (nullable `Text` column). Existing cache files self-heal via `CachedDataStore._migrate_additive_columns` (nullable non-PK columns are `ALTER TABLE`-added on open) — **no `SCHEMA_VERSION` bump**, no cache wipe. The read path (`Filing.model_validate(row, from_attributes=True)`) picks the column up with zero code change.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/backtest/test_cache_store.py` in the `# ── filings ──` section (reuse the existing `store` fixture and `_dt` helper):

```python
def test_filings_round_trip_litigation_excerpt(store: CachedDataStore) -> None:
    """``litigation_excerpt`` must survive the write → read round-trip.

    Phase 14's filing-delta signal diffs litigation language year-over-year;
    a column that silently drops on write would starve the channel for every
    backtest while live EDGAR kept working — exactly the live/replay drift
    the golden cache exists to prevent.  Assert the positive signal (prose
    comes back), not merely the absence of an error.
    """
    filing = Filing(
        ticker="AAPL", form_type="10-K", accession_no="0001-litigation",
        filed_at=_dt(2023, 1, 15), url="https://sec/lit",
        litigation_excerpt="In re Apple Securities Litigation: consolidated claims pending.",
    )

    store.write_filings("AAPL", [filing])

    result = store.read_filings("AAPL", as_of=_dt(2023, 3, 15))

    assert len(result) == 1
    assert result[0].litigation_excerpt is not None
    assert "Apple Securities Litigation" in result[0].litigation_excerpt
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py::test_filings_round_trip_litigation_excerpt -v`
Expected: FAIL — the excerpt reads back as `None` (the write path never persists it; the ORM row has no such column).

- [ ] **Step 3: Add the column and the write-path value**

In `src/backtest/cache/schema.py`, inside `class FilingRow`, after the `mda_excerpt` column:

```python
    mda_excerpt:          str      = Column(Text)
    # Phase 14: Legal Proceedings prose for the filing-delta (Lazy Prices)
    # diff.  Nullable — rows written before Phase 14 read back as None
    # (correct degradation: the render layer marks the section as absent).
    # Existing cache files self-heal via _migrate_additive_columns (nullable
    # ALTER TABLE ADD COLUMN on store open); a refetch then populates it.
    litigation_excerpt:   str      = Column(Text,     nullable=True)
```

In `src/backtest/cache/store.py::write_filings`, extend the `sqlite_insert(FilingRow).values(...)` call — after `mda_excerpt=f.mda_excerpt,` add:

```python
                    # Phase 14 — litigation prose for the filing-delta diff.
                    litigation_excerpt=f.litigation_excerpt,
```

Do **not** bump `SCHEMA_VERSION` — this is exactly the additive-nullable case `_migrate_additive_columns` was built for (see its docstring).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/unit/backtest/test_cache_store.py tests/unit/backtest/test_cache_providers.py -v`
Expected: all PASS (round-trip green; provider tests confirm no regression on the pool/selection read paths).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/cache/schema.py src/backtest/cache/store.py tests/unit/backtest/test_cache_store.py
git commit -m "feat(cache): litigation_excerpt column on filings (additive, self-migrating)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Generalised prior-year diff rendering (MD&A + risk factors + litigation)

**Files:**
- Modify: `src/agents/analysts/fundamental/fetch.py` (replace `_render_mda` with `_render_diffed_section`; rewire the filings loop in `_build_ticker_context`)
- Test: `tests/unit/agents/analysts/fundamental/test_fetch_context_render.py`

**Interfaces:**
- Consumes: `FundamentalCaps.max_filing_litigation_chars` (Task 1), `Filing.litigation_excerpt` on filing dicts (Task 2), existing `_find_prior_year_baseline` + `deboilerplate_mda` (unchanged).
- Produces: `_render_diffed_section(filing: dict, text: str, *, section_field: str, baselines: list[dict], cap_chars: int, stub_threshold: int) -> str` — one render helper for all three prose sections. Context lines: `MD&A: …`, `Risk factors: …`, `Litigation: …`. Marker strings are unchanged from Phase 13 (`[de-boilerplate vs <period>: …]`, `[no prior-year pair: …]`, `[prior-year pair found but text too short to diff — full text]`) — Task 6's prompt keys off exactly these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/analysts/fundamental/test_fetch_context_render.py` (reuse `_empty_bundle`, `_deboilerplate_caps`, `_BOILERPLATE_PARA`, `_CURRENT_UNIQUE_PARA`, `_PRIOR_UNIQUE_PARA` already defined in the file):

```python
# ---------------------------------------------------------------------------
# Tests — Phase 14: risk-factor + litigation diffing, XOM stub fallback
# ---------------------------------------------------------------------------

# Year-over-year risk-factor prose: one boilerplate bullet shared verbatim,
# one genuinely new bullet in the current filing.  Each clears the 50-char
# stub threshold used by _deboilerplate_caps.
_RISK_BOILERPLATE = (
    "Our business is subject to intense competition across all markets in "
    "which we operate, which may adversely affect our results of operations."
)

_RISK_NEW_BULLET = (
    "We are subject to new export-control restrictions announced in March "
    "that materially limit shipments of our highest-margin products to Asia."
)

_LITIGATION_BOILERPLATE = (
    "The company is subject to various legal proceedings arising in the "
    "ordinary course of business, none of which is expected to be material."
)

_LITIGATION_NEW = (
    "In February the Department of Justice filed a civil antitrust complaint "
    "against the company seeking structural remedies in the services segment."
)


class TestRiskAndLitigationDiffing:
    """Risk factors and litigation de-boilerplate against the prior-year pair."""

    def _current_filing(self) -> dict:
        """Return a current 10-Q dict with all three prose sections."""
        return {
            "ticker": "AAPL",
            "form_type": "10-Q",
            "filed_at": "2026-05-01",
            "period_of_report": "20260328",
            "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
            "risk_factors_excerpt": _RISK_BOILERPLATE + "\n\n" + _RISK_NEW_BULLET,
            "litigation_excerpt": _LITIGATION_BOILERPLATE + "\n\n" + _LITIGATION_NEW,
            "body_excerpt": None,
        }

    def _baseline_pool(self) -> list[dict]:
        """Return the prior-year 10-Q carrying the shared boilerplate only."""
        return [
            {
                "ticker": "AAPL", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250329",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": _RISK_BOILERPLATE,
                "litigation_excerpt": _LITIGATION_BOILERPLATE,
                "body_excerpt": None,
            },
        ]

    def _render(self) -> str:
        """Run the context builder with generous diff-friendly caps."""
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_deboilerplate_caps(),
        ):
            return _build_ticker_context(
                ticker="AAPL",
                filings_payload=[self._current_filing()],
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

    def test_risk_factors_are_diffed_against_prior_year(self):
        """The shared risk bullet is stripped; the new bullet survives."""
        result = self._render()

        # Positive signal: the diff fired on the risk section (header names
        # the matched prior period) and the genuinely new bullet survived.
        assert "Risk factors:" in result
        assert "new export-control restrictions" in result

        # The verbatim boilerplate bullet was removed as unchanged.
        assert "intense competition across all markets" not in result

    def test_litigation_is_rendered_and_diffed(self):
        """The litigation line appears, boilerplate stripped, new matter kept."""
        result = self._render()

        assert "Litigation:" in result
        assert "civil antitrust complaint" in result
        assert "ordinary course of business" not in result

    def test_all_three_sections_name_the_matched_prior_period(self):
        """Every diffed section header names 20250329 — one pairing, three diffs."""
        result = self._render()

        # One de-boilerplate header per section (MD&A + risk + litigation).
        assert result.count("[de-boilerplate vs 20250329:") == 3


# An XOM-shaped 10-K MD&A: a cross-reference stub incorporating Exhibit 13 by
# reference.  ~115 chars — under the 200-char stub threshold below.
_XOM_MDA_STUB = (
    "Reference is made to the Financial Section of the 2025 Annual Report, "
    "Exhibit 13, incorporated herein by reference."
)


def _stub_guard_caps() -> FundamentalCaps:
    """Caps with a 200-char stub threshold: the XOM stub (~115 chars) is
    guarded while the ~380-char 10-Q prose fixtures still clear it."""
    llm = LlmCaps(
        timeout_seconds=30,
        max_output_tokens=512,
        temperature=0.3,
        timeout_retries=1,
        schema_retries=1,
    )
    return FundamentalCaps(
        max_filing_mda_chars=12000,
        max_filing_risk_chars=12000,
        max_filing_litigation_chars=12000,
        max_filing_8k_body_chars=200,
        max_insider_footnotes=2,
        max_insider_footnote_chars=100,
        mda_stub_char_threshold=200,
        llm=llm,
    )


class TestIncorporatedByReferenceStubFallback:
    """XOM-style 10-K stub: no diff attempted; the 10-Q pair still diffs."""

    def _payload(self) -> list[dict]:
        """Return a 10-K with a stub MD&A plus a 10-Q with real prose."""
        return [
            {   # The stub 10-K — MD&A incorporated by reference (Exhibit 13).
                "ticker": "XOM", "form_type": "10-K", "filed_at": "2026-02-25",
                "period_of_report": "20251231",
                "mda_excerpt": _XOM_MDA_STUB,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
            {   # The 10-Q — genuine prose that must still diff normally.
                "ticker": "XOM", "form_type": "10-Q", "filed_at": "2026-05-01",
                "period_of_report": "20260331",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _CURRENT_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def _baseline_pool(self) -> list[dict]:
        """Prior-year pool: a stub 10-K (same shape) and a real-prose 10-Q."""
        return [
            {
                "ticker": "XOM", "form_type": "10-K", "filed_at": "2025-02-26",
                "period_of_report": "20241231",
                "mda_excerpt": _XOM_MDA_STUB,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
            {
                "ticker": "XOM", "form_type": "10-Q", "filed_at": "2025-05-02",
                "period_of_report": "20250331",
                "mda_excerpt": _BOILERPLATE_PARA + "\n\n" + _PRIOR_UNIQUE_PARA,
                "risk_factors_excerpt": None,
                "litigation_excerpt": None,
                "body_excerpt": None,
            },
        ]

    def test_stub_10k_gets_marker_and_10q_still_diffs(self):
        """The stub is marked (no fake diff); the 10-Q carries the delta signal.

        This pins the established XOM fallback: an incorporated-by-reference
        MD&A must surface as a NO-COMPARISON marker — never as a de-boilerplate
        header that the prompt would read as 'nothing changed' (quiet-bullish).
        """
        with patch(
            "agents.analysts.fundamental.fetch._caps",
            return_value=_stub_guard_caps(),
        ):
            result = _build_ticker_context(
                ticker="XOM",
                filings_payload=self._payload(),
                insider_bundle=_empty_bundle(),
                insider_lookback_days=30,
                ratios=None,
                baseline_filings_payload=self._baseline_pool(),
            )

        # The stub 10-K: pairing succeeded but the stub guard blocked the diff.
        assert "too short to diff" in result
        # The stub text itself is still shown (the LLM sees WHY there is no diff).
        assert "incorporated herein by reference" in result

        # Positive signal: the 10-Q pair diffed normally in the same context.
        assert "[de-boilerplate vs 20250331:" in result
        assert "record March quarter for iPhone" in result
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_fetch_context_render.py -v -k "RiskAndLitigation or IncorporatedByReference"`
Expected: the risk/litigation tests FAIL (`Litigation:` line absent; risk boilerplate present verbatim; only 1 de-boilerplate header). The XOM test may already pass for MD&A (existing stub guard) but fails on the count/positive-signal assertions until the loop is rewired — confirm at least one red assertion per test class.

- [ ] **Step 3: Replace `_render_mda` with `_render_diffed_section`**

In `src/agents/analysts/fundamental/fetch.py`, delete the `_render_mda` function and add in its place:

```python
def _render_diffed_section(
    filing: dict,
    text: str,
    *,
    section_field: str,
    baselines: list[dict],
    cap_chars: int,
    stub_threshold: int,
) -> str:
    """Return one prose section's text, diffed against the prior-year pair.

    Phase 14 generalisation of the Phase 13 MD&A-only ``_render_mda``: the
    same pairing + paragraph-diff machinery now serves MD&A, risk factors and
    litigation.  Attempts de-boilerplate diffing against the prior-year
    baseline filing when:

    - The current filing has a ``period_of_report`` field.
    - A matching baseline filing exists (same form type, ~365 days earlier).
    - Both the current and prior section texts exceed ``stub_threshold``
      chars (stubs — e.g. an incorporated-by-reference cross-reference — are
      too short to diff meaningfully).

    Falls back to the full text (capped at ``cap_chars``) with a descriptive
    NO-COMPARISON marker in these cases:

    - No ``period_of_report`` on the current filing.
    - No matching baseline found.
    - Either side's text is shorter than ``stub_threshold``.
    - An unexpected exception inside the diff call.

    The marker strings are load-bearing: the Phase 14 prompt tells the LLM
    that any ``[no prior-year pair ...]`` / ``[... too short to diff ...]``
    marker means "comparison unavailable" — which must never be read as
    "nothing changed" (the quiet-bullish branch of the Lazy Prices sign
    convention requires a *performed* diff that found little change).

    Parameters
    ----------
    filing:
        Current filing dict (``Filing.model_dump()`` shape).
    text:
        Stripped section text from the current filing (non-empty; the caller
        guards).
    section_field:
        Which section this is — one of ``"mda_excerpt"``,
        ``"risk_factors_excerpt"``, ``"litigation_excerpt"``.  Used both to
        read the prior-year counterpart off the baseline dict and to label
        log lines.
    baselines:
        Prior-year baseline filing dicts from the pool provider call.
    cap_chars:
        Character cap applied to the rendered output (per-section config
        value from ``FundamentalCaps``).
    stub_threshold:
        Minimum character count for both sides before diffing is attempted
        (``mda_stub_char_threshold`` in config — shared by all sections).

    Returns
    -------
    str
        Either the de-boilerplated (diffed) section text or the full text
        with a fallback marker, capped at ``cap_chars``.
    """
    form_type = filing.get("form_type", "?")
    current_period = filing.get("period_of_report") or ""

    # --- Attempt pairing only when period_of_report is available ---
    if not current_period:
        _logger.debug(
            "_render_diffed_section[%s]: no period_of_report for %s %s — full text",
            section_field, form_type, filing.get("accession_no", "?"),
        )
        return "[no prior-year pair: period_of_report absent — full text]\n\n" + text[:cap_chars]

    # --- Find prior-year baseline (same form type, ~365 days earlier) ---
    baseline = _find_prior_year_baseline(current_period, form_type, baselines)

    if baseline is None:
        _logger.debug(
            "_render_diffed_section[%s]: no baseline for %s period=%s — full text",
            section_field, form_type, current_period,
        )
        return "[no prior-year pair: baseline not in cache — full text]\n\n" + text[:cap_chars]

    prior_text = (baseline.get(section_field) or "").strip()
    prior_period = baseline.get("period_of_report") or "prior year"

    # --- Stub guard: skip diffing if either side is too short ---
    # This is the incorporated-by-reference path (e.g. XOM's 10-K Item 7 is
    # an Exhibit 13 cross-reference stub): the stub is shown verbatim under a
    # marker, and the delta signal comes from the ticker's 10-Q instead.
    if len(text) < stub_threshold or len(prior_text) < stub_threshold:
        _logger.debug(
            "_render_diffed_section[%s]: stub text for %s (current=%d, prior=%d, "
            "threshold=%d) — full text",
            section_field, form_type, len(text), len(prior_text), stub_threshold,
        )
        return "[prior-year pair found but text too short to diff — full text]\n\n" + text[:cap_chars]

    # --- De-boilerplate diff (generic paragraph-fingerprint filter) ---
    try:
        filtered_text, stats = deboilerplate_mda(
            current_text=text,
            prior_text=prior_text,
            algo_version=MDA_DEBOILERPLATE_ALGO_VERSION,
            prior_period_label=prior_period,
        )
        _logger.info(
            "_render_diffed_section[%s]: %s pair=%s→%s dropped=%d/%d "
            "(%.1f%% retained) chars %d→%d",
            section_field,
            form_type,
            prior_period,
            current_period,
            stats["paragraphs_dropped"],
            stats["paragraphs_total"],
            stats["coverage_pct"],
            stats["chars_in"],
            stats["chars_out"],
        )
        return filtered_text[:cap_chars]

    except Exception as exc:
        _logger.warning(
            "_render_diffed_section[%s]: diff failed for %s %s: %s — full text",
            section_field, form_type, current_period, exc,
        )
        return "[de-boilerplate error — full text]\n\n" + text[:cap_chars]
```

- [ ] **Step 4: Rewire the filings loop in `_build_ticker_context`**

Replace the `# --- Filing excerpts ---` loop body (the `if filings_payload:` block) with:

```python
    # --- Filing excerpts ---
    # For annual / quarterly filings (10-K, 10-Q) we render three prose
    # sections — MD&A, risk factors, litigation — each de-boilerplated
    # against the prior-year pair where a fiscal-period match exists
    # (Phase 13 machinery, generalised in Phase 14 for the filing-delta
    # signal).  For event-driven filings (8-K) those sections are absent;
    # instead we render the body_excerpt which captures the catalyst,
    # guidance update, or earnings announcement (Phase 7 audit 2.7).
    if filings_payload:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        for filing in filings_payload:
            form_type = filing.get("form_type", "?")
            filed_at  = filing.get("filed_at", "?")

            mda        = (filing.get("mda_excerpt") or "").strip()
            risk_fac   = (filing.get("risk_factors_excerpt") or "").strip()
            litigation = (filing.get("litigation_excerpt") or "").strip()
            body_expt  = (filing.get("body_excerpt") or "").strip()

            if mda or risk_fac or litigation:
                # 10-K / 10-Q style: periodic prose sections available.
                lines.append(f"  [{form_type}, filed {filed_at}]")

                if mda:
                    lines.append("  MD&A: " + _render_diffed_section(
                        filing, mda,
                        section_field="mda_excerpt",
                        baselines=baselines,
                        cap_chars=caps.max_filing_mda_chars,
                        stub_threshold=mda_stub_threshold,
                    ))

                if risk_fac:
                    lines.append("  Risk factors: " + _render_diffed_section(
                        filing, risk_fac,
                        section_field="risk_factors_excerpt",
                        baselines=baselines,
                        cap_chars=caps.max_filing_risk_chars,
                        stub_threshold=mda_stub_threshold,
                    ))

                if litigation:
                    lines.append("  Litigation: " + _render_diffed_section(
                        filing, litigation,
                        section_field="litigation_excerpt",
                        baselines=baselines,
                        cap_chars=caps.max_filing_litigation_chars,
                        stub_threshold=mda_stub_threshold,
                    ))

            elif body_expt:
                # 8-K style: no periodic sections, but there is a body excerpt
                # capturing the event (earnings, guidance, officer changes —
                # Item 5.02 departures/appointments are part of the Phase 14
                # executive-team-change surface).
                lines.append(f"  [{form_type}, filed {filed_at}]")
                lines.append(f"  Body: {body_expt[:caps.max_filing_8k_body_chars]}")
    else:
        lines.append("-- COMPANY FILINGS (PROSE) --")
        lines.append("  (no filings available)")
```

Also update the module docstring's context-block description (the paragraph starting "Filing excerpts (MD&A + risk factors)") to mention all three diffed sections, and update `_build_ticker_context`'s docstring sentence "attempts to de-boilerplate the MD&A" to "attempts to de-boilerplate the MD&A, risk-factor and litigation sections".

- [ ] **Step 5: Run the full fundamental render/pairing suite**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/ -v`
Expected: all PASS — new classes green; the pre-existing `TestMdaDeboilerplateFiresFromPool` and `test_fetch_baseline_pairing.py` still green (marker strings and pairing signature unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/agents/analysts/fundamental/fetch.py tests/unit/agents/analysts/fundamental/test_fetch_context_render.py
git commit -m "feat(fundamental): diff risk factors + litigation against prior-year pair

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: `horizon_days` across the verdict contracts (canonical + emit) + interim prompt patches

**Files:**
- Modify: `src/contract/evidence.py` (`AnalystVerdict` base default + `LlmTickerVerdict` emit schema)
- Modify: `src/agents/analysts/news/prompts.py` (OUTPUT CONTRACT + SHAPE EXAMPLE — interim patch; Plan 3's news rebuild replaces this prompt wholesale)
- Modify: `src/agents/analysts/fundamental/prompts.py` (OUTPUT CONTRACT — interim patch; Task 6 rewrites this prompt wholesale)
- Modify: `tests/contract/test_llm_ticker_verdict.py`, `tests/unit/contract/test_llm_to_ticker_inflate.py`
- Modify: `tests/unit/agents/analysts/news/test_joiner.py`, `tests/unit/agents/analysts/fundamental/test_joiner.py`
- Modify (fixture sweep): any test file constructing an `LlmTickerVerdict` payload (enumerated in Step 4)

**Interfaces:**
- Produces: `AnalystVerdict.horizon_days: int = Field(default=1, ge=1)` (inherited by `TickerVerdict`) and `LlmTickerVerdict.horizon_days: int = Field(ge=1)` — **required** on the emit schema (no default). Consumed by Task 6's fundamental prompt, the strategist, and the scoreboard; the macro/linkage analyst (Plans 4–5) reads the same field. `to_ticker_verdict()` carries it across with zero code change (field-name-subset invariant).

**Interface being added:**

```python
# On AnalystVerdict (base class — default keeps deterministic analysts valid):
horizon_days: int = Field(default=1, ge=1)

# On LlmTickerVerdict (emit schema, extra="forbid" — REQUIRED, no default):
horizon_days: int = Field(ge=1)
```

`horizon_days` is the number of **trading days** the analyst expects its lean to
remain valid. Deterministic analysts (technical) inherit the default of 1 (their
verdicts are recomputed every tick). The news analyst must state it explicitly:
~5 for a fresh-surprise lean, longer for drift continuation (spec §5 news-rebuild design /
literature table: PEAD 5–90d).

`horizon_days` is REQUIRED on `LlmTickerVerdict` deliberately: the emit schema's
doctrine is "structured commitments before prose, nothing optional the model can
lazily omit". `to_ticker_verdict()` inflates via `model_dump()` →
`model_validate()`, so the field carries across automatically once it exists on
both classes — no inflation-code change needed.

**Steps:**

- [ ] **Step 1: Write the failing contract tests.**

In `tests/contract/test_llm_ticker_verdict.py`, first update the shared
`_valid_emit_payload` helper: add `"horizon_days": 5,` immediately after the
`"is_no_data"` entry (keep the payload's field order matching the schema's
declaration order). Then append these tests at the end of the file (add
`AnalystVerdict` to the existing `from contract.evidence import ...` line):

```python
def test_horizon_days_is_required_on_the_llm_emit():
    """The news prompt must commit to a horizon — the schema enforces it."""
    payload = _valid_emit_payload()
    del payload["horizon_days"]

    with pytest.raises(ValidationError):
        LlmTickerVerdict.model_validate(payload)


def test_horizon_days_must_be_at_least_one_trading_day():
    """A zero or negative horizon is meaningless — ge=1 rejects it."""
    payload = _valid_emit_payload()
    payload["horizon_days"] = 0

    with pytest.raises(ValidationError):
        LlmTickerVerdict.model_validate(payload)


def test_to_ticker_verdict_carries_horizon_days():
    """Inflation to the full TickerVerdict must not drop the horizon."""
    payload = _valid_emit_payload()
    payload["horizon_days"] = 7

    verdict = LlmTickerVerdict.model_validate(payload)

    assert verdict.to_ticker_verdict().horizon_days == 7


def test_analyst_verdict_defaults_horizon_to_one_day():
    """Deterministic analysts never set a horizon — the base default is 1."""
    verdict = AnalystVerdict(
        lean="neutral", magnitude=0.0, confidence=0.0,
        rationale="deterministic baseline",
    )

    assert verdict.horizon_days == 1
```

Run and watch them fail (missing-field / unexpected-attribute errors):

```bash
.venv/bin/python -m pytest tests/contract/test_llm_ticker_verdict.py -v
```

- [ ] **Step 2: Add the inflation-path failing tests.**

In `tests/unit/contract/test_llm_to_ticker_inflate.py`:

1. Update the `_make_llm_verdict` helper — add `horizon_days = 60,` to the `LlmTickerVerdict(...)` construction (after `confidence`).
2. Append two tests:

```python
def test_to_ticker_verdict_propagates_horizon_days():
    """The emit-schema horizon must survive inflation to the canonical shape.

    Phase 14: the fundamental analyst emits horizon_days=60 (Lazy Prices
    drift window); the strategist and scoreboard read it off TickerVerdict.
    A silent drop here would flatten every long-horizon signal to the
    canonical default of 1 — assert the positive value, not just presence.
    """
    llm = _make_llm_verdict()

    canonical = llm.to_ticker_verdict()

    assert canonical.horizon_days == 60


def test_horizon_days_is_required_on_llm_emit():
    """Omitting horizon_days must fail validation loudly.

    Required-by-schema (not defaulted) so the JSON Schema sent to Vertex
    marks it mandatory — the constrained decoder cannot take the shortest
    legal path that omits it (2026-05-25 audit failure mode).
    """
    import pytest
    from pydantic import ValidationError

    payload = _make_llm_verdict().model_dump()
    payload.pop("horizon_days")

    with pytest.raises(ValidationError):
        LlmTickerVerdict.model_validate(payload)
```

Run both new test files and watch them fail (missing field / `extra="forbid"`):

```bash
.venv/bin/python -m pytest tests/contract/test_llm_ticker_verdict.py tests/unit/contract/test_llm_to_ticker_inflate.py -v
```

- [ ] **Step 3: Add the field to both classes in `src/contract/evidence.py`.**

In `AnalystVerdict`, immediately after the `is_no_data: bool = False` line:

```python
    # Phase 14: how many TRADING DAYS the analyst expects this lean
    # to remain valid.  Deterministic analysts recompute every tick and keep
    # the default of 1; drift-aware analysts (news, macro) state it
    # explicitly — ~5 for a fresh surprise, longer for drift continuation.
    horizon_days: int = Field(default=1, ge=1)
```

In `LlmTickerVerdict`, immediately after its `is_no_data: bool` line (i.e. BEFORE
the `key_factors` block — structured commitments stay ahead of prose):

```python
    # Trading days the lean should hold.  REQUIRED — the emit schema never
    # lets the model lazily omit a structured commitment.  Inflation to
    # TickerVerdict carries it across via model_dump()/model_validate().
    # Vertex's constrained decoder omits optional fields, and an omitted
    # horizon would silently collapse to the canonical default (1) and
    # flatten every long-horizon signal — the exact silent-degradation class
    # this codebase raises on.  The fundamental prompt (Task 6) instructs a
    # fixed config-driven value (``filing_delta_horizon_days``); the news
    # prompt emits 1 until Plan 3's rebuild reframes it.
    horizon_days: int = Field(ge=1)
```

- [ ] **Step 4: Fixture sweep.**

`LlmTickerVerdict` is `extra="forbid"` with `horizon_days` now required, so every
hand-built emit payload in the test suite needs the key. Run the full suite and
fix every fixture that now fails validation with "horizon_days — Field required"
(add `horizon_days=60` in fundamental-flavoured fixtures, `horizon_days=1` in
news-flavoured ones; for raw emit *dicts*, add the JSON key):

```bash
.venv/bin/python -m pytest tests/ -q 2>&1 | tail -30
grep -rln "LlmTickerVerdict(\|LlmTickerVerdict\|temp:news_verdict_\|temp:fundamental_verdict_" tests/
```

Known construction/fixture sites to update:

- `tests/contract/test_llm_ticker_verdict.py` (done in Step 1)
- `tests/unit/contract/test_llm_to_ticker_inflate.py` (done in Step 2)
- `tests/integration/backtest/conftest.py`
- `tests/integration/test_fundamental_canned_output.py`
- `tests/unit/agents/analysts/test_cache_callbacks_per_ticker.py`
- `tests/unit/agents/analysts/test_per_ticker_branch.py`
- `tests/unit/agents/test_output_caps_per_ticker.py`

One additional targeted edit while in the sweep:
`tests/unit/agents/analysts/fundamental/test_joiner.py` — add `"horizon_days": 60,`
to the two raw `temp:fundamental_verdict_*` dicts (after `"confidence"`), and in
`test_joiner_synthesises_no_data_for_missing_key` add one assertion after the
existing `assert msft_verdict["is_no_data"] is True` line:

```python
    # Phase 14: a synthesised no-data verdict carries the canonical default
    # horizon (1) — the long fundamental horizon applies only to real emits.
    assert msft_verdict["horizon_days"] == 1
```

Let pytest be the arbiter — fix exactly what fails, nothing speculative:

```bash
.venv/bin/python -m pytest tests/ -v
```

- [ ] **Step 5: Interim prompt compatibility.**

Until Plan 3's rebuild rewrites the news prompt (and Task 6 rewrites the
fundamental prompt), both LLM analysts emit payloads WITHOUT `horizon_days` and
would now fail schema validation at runtime. Keep them valid with an interim
patch to each prompt's OUTPUT CONTRACT.

In `src/agents/analysts/news/prompts.py` (the OUTPUT CONTRACT block, ~line 59),
insert a `horizon_days` line between the `confidence` and `is_no_data` lines:

```
  confidence    ∈ [0, 1]
  horizon_days  integer ≥ 1 — trading days you expect this lean to hold.
                Emit 1 unless the evidence clearly supports a longer hold.
  is_no_data    boolean — true ONLY if the headlines block is empty for this
```

And in the same file's SHAPE EXAMPLE JSON block, add a `"horizon_days": 1,` line immediately after the `"confidence": <0.0-1.0>,` line (match the example's existing formatting).

In `src/agents/analysts/fundamental/prompts.py`, add to the OUTPUT CONTRACT's
field descriptions (matching the file's existing list formatting):

```
- horizon_days: integer >= 1 — trading days you expect this lean to hold.
```

This is a deliberate interim patch: the emit schema is shared with the news and
fundamental analysts, and without these lines the current LLMs would hit schema
retries until their real rewrites land (Plan 3 for news, Task 6 for fundamental).
Note: editing the prompts auto-flips `NEWS_PROMPT_VERSION` /
`FUNDAMENTAL_PROMPT_VERSION` (they are derived by hashing the rendered prompt in
`report_cache.py`), which correctly invalidates any on-disk report-cache entries
that lack the new field — old cached verdicts can never hit the now-stricter
schema gate.

- [ ] **Step 6: Add a joiner propagation test** (spec §8: "horizon field propagation").

Append to `tests/unit/agents/analysts/news/test_joiner.py`, reusing
that file's existing imports and session/context harness (mirror the state
fixture shape its existing happy-path test uses — the load-bearing part is the
final assertion):

```python
@pytest.mark.asyncio
async def test_joiner_propagates_horizon_days_into_the_verdict_batch():
    """horizon_days must survive the joiner's validate→inflate→dump round trip."""
    svc = InMemorySessionService()
    session = await svc.create_session(
        app_name="test",
        user_id="test",
        state={
            "tickers": ["AAPL"],
            "tick_id": "t-1",
            "as_of": "2026-07-06T14:00:00",
            "temp:news_data": {"AAPL": {"news": []}},
            "temp:news_verdict_AAPL": {
                "ticker": "AAPL",
                "lean": "bullish",
                "magnitude": 0.4,
                "confidence": 0.6,
                "is_no_data": False,
                "horizon_days": 5,
                "key_factors": ["catalyst:earnings"],
                "report": {
                    "summary": "Genuine positive surprise; positioning for drift.",
                    "drivers": [
                        {"name": "eps_beat", "direction": "bull", "weight": 0.6,
                         "body": "EPS well above consensus."},
                        {"name": "guidance", "direction": "bull", "weight": 0.4,
                         "body": "Full-year guidance raised."},
                    ],
                },
            },
        },
        session_id="t-1",
    )

    agent = NewsJoinerAgent(name="NewsJoiner")
    ctx = InvocationContext(
        session_service=svc, session=session, invocation_id="inv-1", agent=agent,
    )

    events = [ev async for ev in agent.run_async(ctx)]

    batch = events[-1].actions.state_delta["news_verdicts"]
    assert batch["verdicts"][0]["horizon_days"] == 5
```

- [ ] **Step 7: Verify and commit.**

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -m ruff check src/ tests/
git add -A
git commit -m "feat(contract): add horizon_days across AnalystVerdict and LlmTickerVerdict

Canonical default on AnalystVerdict; required on the LlmTickerVerdict emit
schema (no default — Vertex omits optionals). Interim prompt patches keep the
news and fundamental emits valid until their real rewrites land.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Diff-oriented fundamental prompt (Lazy Prices sign convention + horizon emission)

**Files:**
- Modify: `src/agents/analysts/fundamental/prompts.py` (`_TEMPLATE`, `build_fundamental_instruction`)
- Test: `tests/unit/agents/analysts/fundamental/test_prompts.py`

**Interfaces:**
- Consumes: `FundamentalCaps.filing_delta_horizon_days` (Task 1), `LlmTickerVerdict.horizon_days` (Task 5), the render markers and `Litigation:` line (Task 4).
- Produces: the rewritten instruction. `FUNDAMENTAL_PROMPT_VERSION` auto-derives from the rendered template at import time (`report_cache._compute_version_constants`), so every cached report is busted automatically — no manual version bump.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/analysts/fundamental/test_prompts.py`:

```python
def test_instruction_carries_filing_delta_horizon():
    """The prompt must name horizon_days and the config-driven value (60).

    Phase 14: the emit schema requires horizon_days; the prompt is where the
    LLM learns WHAT to emit.  The value must come from config
    (fundamental.filing_delta_horizon_days), never be hardcoded in the
    template, so a config change re-tunes the horizon without a code edit.
    """
    from config.analysts import get_analysts_config

    instruction = build_fundamental_instruction(_vocab())

    horizon = get_analysts_config().fundamental.filing_delta_horizon_days

    assert "horizon_days" in instruction
    assert str(horizon) in instruction


def test_instruction_states_lazy_prices_sign_convention():
    """The diff-oriented sign convention must be stated, both branches.

    Substantive year-over-year change → bearish by default; a performed diff
    that found essentially nothing → quiet-bullish.  Greppable phrases pin
    the doctrine so a future prompt edit cannot silently drop it.
    """
    instruction = build_fundamental_instruction(_vocab())

    assert "BEARISH by default" in instruction
    assert "quiet-bullish" in instruction


def test_instruction_forbids_reading_markers_as_no_change():
    """NO-COMPARISON markers must be excluded from the quiet-bullish branch.

    An incorporated-by-reference stub (XOM 10-K) or a missing prior-year pair
    renders a marker, not a diff.  The prompt must tell the LLM that markers
    mean 'comparison unavailable' — treating them as 'nothing changed' would
    manufacture quiet-bullish leans from data gaps.
    """
    instruction = build_fundamental_instruction(_vocab())

    assert "no prior-year pair" in instruction
    assert "too short to diff" in instruction
    assert "NOT evidence of stability" in instruction
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_prompts.py -v`
Expected: 3 new tests FAIL (phrases absent); 3 pre-existing tests PASS.

- [ ] **Step 3: Replace `_TEMPLATE` wholesale**

In `src/agents/analysts/fundamental/prompts.py`, replace the entire `_TEMPLATE` string with the following (the OUTPUT CONTRACT, hard rules R1/R2, insider doctrine, valuation anchor and going-concern override are retained from Phase 9/13 — do not drop them; the filings sections and lean-forming doctrine are rewritten diff-first):

```python
_TEMPLATE = """You are the Fundamental analyst — a FILING-DELTA analyst.

You are focused on a SINGLE ticker for this call: {ticker}

Your core question is NOT "is this a good company?"  It is:

    WHAT CHANGED in this company's SEC filings since the previous
    comparable filing (10-K vs prior 10-K, 10-Q vs year-ago 10-Q)?

The evidence base (Cohen, Malloy & Nguyen 2020, "Lazy Prices"): firms that
substantively change the language of their periodic filings — MD&A, risk
factors, litigation, executive-team disclosures — systematically
underperform over the following 3–6 months, because the market is slow to
price changes buried in long documents.  Firms whose filings are near-
verbatim repeats of last year's quietly outperform.  You are positioned to
capture that drift: your verdict targets the NEXT 3–6 MONTHS, not the next
session.

The data block for {ticker} contains:

  -- COMPANY RATIOS (SCALAR) --
    Non-null scalar fundamentals only.  In practice this block reliably
    carries: trailing P/E, beta, sector (for sector-relative valuation),
    revenue growth YoY, profit margin, ROE, free cash flow, debt/equity,
    and price reference (50/200-day average, 52-week high/low).
    Forward-looking and consensus fields (forward P/E, PEG, analyst rating,
    analyst-opinion count) are NOT available in this feed — do NOT reason
    as if they were provided, and NEVER invent a numeric forward estimate.

  -- COMPANY FILINGS (PROSE) --
    Up to three diffed sections per periodic filing (10-K / 10-Q):
      MD&A: ...          Risk factors: ...          Litigation: ...
    Each section has been de-boilerplated against the SAME SECTION of the
    previous comparable filing (same form type, one fiscal year earlier):
    paragraphs that match the prior filing verbatim have been REMOVED.
    Every paragraph you see under a "[de-boilerplate vs <period>: ...]"
    header is NEW or CHANGED year-over-year — that header also tells you
    how many paragraphs were dropped, i.e. how much of the document is
    unchanged boilerplate.
    8-K filings render a body excerpt instead (catalyst, earnings, guidance,
    or an Item 5.02 officer departure/appointment).

    MARKER SEMANTICS — read carefully:
      "[de-boilerplate vs <period>: N of M paragraphs removed]"
          → a comparison WAS performed.  What follows is the year-over-year
            delta.  Little surviving text = the filing barely changed.
      "[no prior-year pair: ...]" or "[... too short to diff — full text]"
          → NO comparison was performed (missing pair, or the section is a
            cross-reference stub, e.g. an MD&A "incorporated by reference"
            to an exhibit).  The full/stub text is shown so you can see why.
            This is NOT evidence of stability — you cannot conclude "nothing
            changed" from a comparison that never happened.  When a 10-K
            section is a stub, judge the delta from the 10-Q sections in
            this same block instead; if no section carries a genuine diff,
            treat the filing-delta signal as ABSENT for this ticker.

  -- INSIDER ACTIVITY (30d, structured) --
    Net Form-4 dollars (+ = net buy, − = net sell), buy/sell counts,
    cluster flags (≥ 3 distinct filers on one side), conviction flags
    (single filer above the dollar threshold), planned-sale ratio (10b5-1),
    top filer role, derivative counts.

  -- INSIDER FOOTNOTES (≤5, prose) --
    Free-text footnotes attached to individual Form 4 rows.

Closed vocabulary (use these tags ONLY in key_factors):

  guidance:<value>            ∈ {guidance_options}
  tone:<value>                ∈ {tone_options}
  risk:<value>                ∈ {risk_tags}
                                 (optionally suffixed with _added | _removed | _intensified
                                  when comparing against the prior filing in the dump)
  insider:<value>             ∈ {insider_signals}
  going_concern:true          when going-concern language is present

OUTPUT CONTRACT
---------------
You MUST emit every field listed below.  ``is_no_data`` and ``report`` are
REQUIRED on every call — there is no shorter legal output.  Emit fields in
this exact order:

  ticker        string — MUST be exactly "{ticker}"
  lean          ∈ {{bullish, bearish, neutral}}
  magnitude     ∈ [0, 1]
  confidence    ∈ [0, 1]
  horizon_days  integer — emit exactly {filing_horizon_days}.  This is the
                trading-day drift window of the filing-delta signal
                (3–6 months); it is fixed for this analyst.
  is_no_data    boolean — true ONLY if BOTH the filings-prose block AND the
                insider-activity block are empty for this ticker; false in
                every other case (including ambiguous data).
  key_factors   list of closed-vocabulary tags — at least 1, at most 8.
  report        object with summary + drivers (schema below).  REQUIRED on
                every emit, including when is_no_data=true (then summary is
                "no filings or insider data" and drivers describe the absence).

Report schema:
  summary  string — argue your lean from the year-over-year deltas.  End
           with one sentence naming the specific evidence that would flip
           your lean — a named filing change, metric, or insider threshold,
           not "if fundamentals deteriorate".  As brief as you like; hard
           upper limit of {summary_max} characters; do not pad.
  drivers  list of 2-4 entries.  Each driver:
    name       string — short label, ≤{driver_name_max} chars.  Do not pad.
    direction  ∈ {{bull, bear, neutral}}
    weight     ∈ [0, 1] — relative importance vs other drivers; should sum
               roughly to 1.0 but is not strictly normalised.
    body       string — prose explanation.  As brief as you like; hard upper
               limit of {driver_body_max} chars; do not pad.  Do NOT cite
               source URLs; synthesise.

The report is your reasoning; the verdict is your conclusion.  They must be
consistent — the lean and direction-weighted driver mix should agree.

SHAPE EXAMPLE (placeholders only — fill from the actual filings + insider data):
{{
  "ticker": "{ticker}",
  "lean": "<bullish|bearish|neutral>",
  "magnitude": <0.0-1.0>,
  "confidence": <0.0-1.0>,
  "horizon_days": {filing_horizon_days},
  "is_no_data": false,
  "key_factors": ["<closed-vocab tag>", "..."],
  "report": {{
    "summary": "<one short paragraph arguing the lean from the filing deltas>",
    "drivers": [
      {{ "name": "<short label>", "direction": "<bull|bear|neutral>",
         "weight": <0.0-1.0>, "body": "<prose; cite the evidence>" }},
      {{ "name": "<short label>", "direction": "<bull|bear|neutral>",
         "weight": <0.0-1.0>, "body": "<prose; cite the evidence>" }}
    ]
  }}
}}

THE SIGN CONVENTION (Lazy Prices) — this is the core doctrine
-------------------------------------------------------------
  Substantive year-over-year CHANGE is BEARISH by default.
  Companies add and sharpen language when something is wrong: a new risk
  bullet, an intensified hedge, a new legal proceeding, a recharacterised
  demand outlook, an executive departure.  They rarely add prose to
  celebrate.  Unless the surviving paragraphs are unambiguously positive
  (e.g. a removed risk bullet, litigation resolved in the company's
  favour, a concrete upgrade in commitment language), score substantive
  change as bearish over the {filing_horizon_days}-day horizon.

  Genuine ABSENCE of change is quiet-bullish.
  A performed diff whose header shows nearly all paragraphs removed as
  unchanged — with only trivial survivors (dates, share counts, rote
  updates) — is the "no news is good news" branch: lean bullish with
  MODEST magnitude (≤ 0.4) and moderate confidence.  This branch requires
  a PERFORMED comparison: a "[no prior-year pair ...]" or "[... too short
  to diff ...]" marker is NOT evidence of stability (see marker semantics
  above) and must never produce the quiet-bullish lean on its own.

Hard rules (override the heuristics below)
------------------------------------------
These are NOT soft guidance.  If the evidence falls under one of these
rules, apply the rule and do NOT reason your way around it.

  R1.  10b5-1 dominant insider selling is NOT bearish.
       If the insider block reports planned_sale_ratio >= 0.80, treat the
       entire insider signal as neutral noise — regardless of dollar
       magnitude, the seller's role, or the raw sell-count.  Pre-scheduled
       sales carry no information about management's view of the price.
       You may mention them in the summary, but they MUST NOT drive a
       bearish lean or appear as a bearish driver.

  R2.  Boilerplate risk-factor language is NOT evidence.
       The mere presence of a topic (competition, regulation, supply
       chain, macro) in the risk-factors section is not evidence in either
       direction — every 10-K mentions these.  Only a NEW bullet, an
       INTENSIFIED bullet, or a REMOVED bullet (vs the prior filing —
       i.e. a paragraph that SURVIVED the diff) counts as risk-factor
       evidence.

How to analyse the evidence
---------------------------
Form your lean for the expected price direction over roughly the next
{filing_horizon_days} trading days (3–6 months).  The filing delta is your
PRIMARY signal; ratios, 8-Ks and insider activity qualify and corroborate
it.

1. Read the deltas first — section by section.
   For each diffed section, ask: what did the company ADD, SHARPEN, or
   REMOVE?
     - MD&A survivors: commitment downgrades ("we are confident" → "we
       expect" → "we may"), tense shifts (forward → historical), hedge
       inflation ("subject to", "could", "potentially") — each is a
       bearish delta even when the headline number is unchanged.
     - Risk-factor survivors: a genuinely new bullet is high-signal
       bearish; an intensified bullet ("could materially" → "will likely
       materially") is moderate bearish; a REMOVED bullet is moderate
       bullish.
     - Litigation survivors: a new proceeding, a regulator escalation, or
       a materially increased loss contingency is bearish; a resolved or
       de-scoped matter is bullish.
     - Executive-team language: departures of the CEO/CFO or auditor
       changes — surfacing in filing prose or in an 8-K Item 5.02 body —
       count as substantive change (bearish by default, per the sign
       convention).
   Weigh the VOLUME of change too: each diff header reports how many
   paragraphs survived.  Heavy survival across sections = a heavily
   rewritten filing = stronger bearish prior.

2. Anchor on EXPECTATIONS — the price already reflects a view.
   Your verdict is about the STOCK, not the company.  Read the COMPANY
   RATIOS block and judge the trailing multiple relative to the company's
   own history AND its sector.  Rich multiple + heavily changed filing is
   doubly bearish; depressed multiple + unchanged filing is the classic
   quiet re-rate setup.  A very high trailing P/E flagged as "POSSIBLY
   DISTORTED BY ONE-TIME EPS ITEM" must NOT be treated as expensive —
   judge valuation qualitatively from the prose and lower confidence.
   Beta is a risk lens, not a directional signal: on a high-beta name your
   drift call is more exposed to being swamped by market moves — size
   confidence accordingly.

3. Insider activity — the asymmetry is the signal.
   Insiders sell for many innocent reasons; they buy with discretionary
   cash for one.  A single open-market executive BUY is high-quality
   bullish; a cluster or a conviction_buy flag is very high-quality.
   Discretionary sales — especially clusters of senior officers — are
   bearish, scaled by size.  Routine 10b5-1 sales are neutral noise (R1).
   The net Form-4 dollars line is signed: + means net buying.  Absence of
   insider activity is genuinely neutral.  Use insiders to CORROBORATE or
   TEMPER the filing-delta read: insider buying into a changed filing is a
   genuine counter-signal worth acknowledging; insider silence leaves the
   delta signal standing alone.

4. Going-concern language — overrides everything.
   Any going-concern disclosure ("substantial doubt about the company's
   ability to continue") is strongly bearish and dominates all other
   signals.  Do not weigh counter-evidence.

Forming the lean — do not default to neutral.
---------------------------------------------
- The right question is "what is the dominant delta here?", not "do all
  signals agree?".
- Substantive change in ANY diffed section → bearish lean unless the
  surviving text is unambiguously positive.  Acknowledge counters (e.g.
  insider buying) in the summary rather than washing to neutral.
- Performed diff, trivial survivors, no contrary insider signal →
  quiet-bullish: lean bullish, magnitude ≤ 0.4, moderate confidence.
- Only use ``lean=neutral`` when the comparison machinery gave you nothing
  to stand on: no performed diff in any section (markers only), no 8-K
  catalyst, and no insider signal — OR when truly equal-and-opposite
  signals cancel.  "I'm not sure" is low confidence on a directional lean,
  not a neutral lean; but "no comparison was possible" IS a legitimate
  neutral with low confidence.
- Calibrate confidence separately from lean.  Confidence = how likely this
  lean predicts the drift over the next {filing_horizon_days} trading
  days.  High (≥0.7): heavy, unambiguous filing rewrite (or its clean
  absence) corroborated by insiders or valuation.  Moderate (0.4–0.6): one
  solid section-level delta.  Low (≤0.35): tone-only reads, stub-marker
  tickers, thin survivors of ambiguous direction.
- Sparse input → humility.  Stub filings + empty insider block = little to
  stand on: lean neutral or weakly directional with confidence ≤ 0.5.
  Excluded evidence (R1/R2) is not weak evidence — it is absent evidence.

Stop emitting if you are about to repeat a token or symbol three or more times in a row.  Return the verdict as-is and never emit filler tokens.

--- TICKER DATA FOR {ticker} ---
{fundamental_context}
"""
```

- [ ] **Step 4: Substitute the horizon in `build_fundamental_instruction`**

In the same file, extend the `_TEMPLATE.format(...)` call inside `build_fundamental_instruction` — after the `driver_body_max` argument, add:

```python
        # Phase 14: the fixed trading-day drift horizon the LLM must emit as
        # ``horizon_days``.  Config-driven so re-tuning needs no code change.
        filing_horizon_days = get_analysts_config().fundamental.filing_delta_horizon_days,
```

Also update the module docstring's first paragraph to describe the analyst as diff-oriented (filing-delta / Lazy Prices), and the `build_fundamental_instruction` docstring to mention the new substitution. The two runtime placeholders (`{fundamental_context}`, `{ticker}`) are untouched.

- [ ] **Step 5: Run the prompt + report-required tests**

Run: `.venv/bin/python -m pytest tests/unit/agents/analysts/fundamental/test_prompts.py tests/unit/test_fundamental_prompt_report_required.py -v`
Expected: all PASS — new doctrine tests green; pre-existing single-ticker / output-contract / caps tests green (the contract block keeps `OUTPUT CONTRACT`, `REQUIRED`, `is_no_data`, `report`, and the cap substitutions).

- [ ] **Step 6: Run the full suite (prompt-version ripple check)**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS. `FUNDAMENTAL_PROMPT_VERSION` re-derives automatically from the new template — anything asserting a *fixed* version string would surface here (none is expected to).

- [ ] **Step 7: Commit**

```bash
git add src/agents/analysts/fundamental/prompts.py tests/unit/agents/analysts/fundamental/test_prompts.py
git commit -m "feat(fundamental): diff-oriented Lazy Prices prompt with horizon_days emission

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Cache refetch + end-to-end verification (operator steps)

**Files:**
- No source changes. Operates on `backtests/cache/store.sqlite` via `scripts/backtest_fetch.py`.

**Interfaces:**
- Consumes: Tasks 2–3 (litigation flows fetch → cache), `--refetch-domain filings` (existing CLI, clears + re-writes a domain's rows per window).
- Produces: refetched filings rows with `litigation_excerpt` populated for the eval windows.

- [ ] **Step 1: Lint and full-suite gate**

```bash
.venv/bin/python -m ruff check src/ tests/ scripts/
.venv/bin/python -m pytest tests/ -q
```

Expected: ruff clean; full suite green.

- [ ] **Step 2: Confirm the additive migration on the real cache (read-only)**

```bash
sqlite3 backtests/cache/store.sqlite "PRAGMA table_info(filings);"
```

If `litigation_excerpt` is absent, open the store once through the code path (the migration runs on store open) and re-check:

```bash
PYTHONPATH=src .venv/bin/python -c "
from pathlib import Path
from backtest.cache.store import CachedDataStore
CachedDataStore(Path('backtests/cache/store.sqlite'))
print('store opened — additive migration applied')
"
sqlite3 backtests/cache/store.sqlite "PRAGMA table_info(filings);"
```

Expected: the column list includes `litigation_excerpt` (all values NULL until refetch).

- [ ] **Step 3: Refetch the filings domain per eval window — DESTRUCTIVE, requires explicit user go-ahead**

The refetch **deletes and re-fetches** every filings row for the window (established house rule: show the command and wait for an explicit "go" — do not run it on your own initiative). One command per window, network-bound (EDGAR rate limits — expect several minutes each):

```bash
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window baseline-2025-09 --refetch-domain filings
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window iran-conflict-2026-02 --refetch-domain filings
PYTHONPATH=src .venv/bin/python -m scripts.backtest_fetch --window long-baseline-2025 --refetch-domain filings
```

Note: `long-baseline-2025` also has a *pending* full refetch for the revenue-concept fix — coordinate with the user on whether to fold this filings refetch into that run rather than doubling EDGAR traffic.

- [ ] **Step 4: Verify litigation populated (positive signal, not just no-error)**

```bash
sqlite3 backtests/cache/store.sqlite "
SELECT form_type,
       SUM(CASE WHEN litigation_excerpt IS NOT NULL THEN 1 ELSE 0 END) AS with_litigation,
       COUNT(*) AS total
FROM filings
WHERE form_type IN ('10-K', '10-Q')
GROUP BY form_type;"
```

Expected: `with_litigation` is materially non-zero for both forms. Not every periodic filing carries the section, but a **zero** count after a refetch means the section keys are wrong (revisit Task 2 Step 5) — do not proceed to eval with a zero count. Spot-check one row's prose:

```bash
sqlite3 backtests/cache/store.sqlite "
SELECT ticker, form_type, substr(litigation_excerpt, 1, 200)
FROM filings
WHERE litigation_excerpt IS NOT NULL
LIMIT 3;"
```

- [ ] **Step 5: Commit (docs/bookkeeping only, if anything changed)**

No source files should be dirty at this point. If the run surfaced notes worth recording, add them to the Phase 14 docs folder per the docs convention; otherwise this task ends with the verification output pasted into the session for the user.

---

## Self-review (performed)

- **Spec coverage (spec §5 filing-delta design + task scope):** previous-comparable retrieval on both paths — already existing (Phase 13 pool machinery), verified and extended with the new section through EDGAR (Task 2), cache schema + refetch (Tasks 3, 7); diff-oriented prompt across MD&A / risk factors / litigation / executive-team language (Tasks 4, 6 — executive-team changes surface via 8-K Item 5.02 bodies and filing prose; 10-K Item 10 is proxy-incorporated and deliberately not fetched); sign convention (Task 6); XOM stub fallback (Tasks 4, 6); verdict through the existing `fundamental` stream (no pipeline changes anywhere); `horizon_days` = 60 via config (Tasks 1, 5, 6). Testing section of the spec (§8: filing-pair selection unit tests, positive-signal assertions) covered in Tasks 2–6.
- **Placeholder scan:** no TBD/TODO/"similar to Task N"; every code step carries the actual code; the one intentionally conditional step (Task 2 Step 5 key verification) specifies the expected values, the exact check command, and the concrete corrective action.
- **Type consistency:** `litigation_excerpt: str | None` uniform across `Filing`, `FilingRow` (`Text`, nullable), `write_filings`, render dicts; `_render_diffed_section(filing: dict, text: str, *, section_field: str, baselines: list[dict], cap_chars: int, stub_threshold: int) -> str` matches all three call sites; `horizon_days: int = Field(ge=1)` on `LlmTickerVerdict` vs `Field(default=1, ge=1)` on `AnalystVerdict` (both added in Task 5); config names (`max_filing_litigation_chars`, `filing_delta_horizon_days`) identical in `FundamentalCaps`, JSON, README, prompt substitution and tests.
