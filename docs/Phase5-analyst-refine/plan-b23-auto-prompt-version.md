# B23 — Auto-derived prompt-version fingerprint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the hand-maintained `NEWS_PROMPT_VERSION` / `FUNDAMENTAL_PROMPT_VERSION` string constants in `src/agents/analysts/report_cache.py` with values auto-derived at import time from a hash of each analyst's rendered prompt instruction, so editing a prompt template automatically invalidates every cached entry.

**Architecture:** Add a pure `_derive_prompt_version(instruction: str) -> str` helper to `report_cache.py`. At import time, that module calls `build_news_instruction(load_heuristics().news_vocabulary)` and `build_fundamental_instruction(load_heuristics().fundamental_vocabulary)` to render each analyst's instruction string, then hashes the rendered output. The two module-level constants become computed values (still named `NEWS_PROMPT_VERSION` / `FUNDAMENTAL_PROMPT_VERSION`, still imported the same way by every call-site) so no downstream code changes — the auto-derivation is invisible to the rest of the codebase.

**Tech Stack:** Python 3.14 · pydantic v2 · pytest · `hashlib.blake2b` (already used in this file for input hashes).

---

## Embedded mini-spec — design decisions

The backlog entry [[B23]] left three design questions open. These are answered below; the implementation in this plan follows these decisions verbatim.

### 1. What to hash

Hash **only the rendered instruction string** returned by `build_news_instruction()` / `build_fundamental_instruction()`.

**Why this is sufficient:**

- `build_*_instruction()` already substitutes (a) the closed-vocabulary lists from `analyst_heuristics.json`, (b) the rationale char-cap from `analysts.json::output_caps`, and (c) all the boilerplate prompt text. So any change to the template, the vocabulary, OR the analyst output caps automatically flows into the rendered string and therefore into the hash.
- We get template + vocab + caps coverage in a single hash with no extra wiring.

**Out of scope for v1:** Pydantic schema fingerprint (`AnalystVerdict` / `AnalystReport.model_json_schema()`). It would catch contract drift but solves a problem we haven't seen — silent-stale-cache from a Pydantic schema edit. Easy to add later by composing `schema_cls.model_json_schema()` into the hash payload.

### 2. Reference-vocab problem

**No reference vocab needed.** The vocab loaded by `load_heuristics()` is deterministic within a process (it's an `lru_cache(maxsize=1)` over a JSON file), so calling `build_news_instruction(load_heuristics().news_vocabulary)` at import time produces a stable string. We hash that string once at module load; the result is the version constant.

A vocab edit in `config/analyst_heuristics.json` changes the rendered string at next process start → version changes → cache invalidates. Exactly the behaviour we want, no `_REFERENCE_VOCAB` constant required.

### 3. Backtest compatibility

Backtest harness pins the version at run start (the constants are computed once at import time and don't change during the run). A mid-sweep prompt edit would require a Python process restart to take effect — which is the right behaviour, because the new prompt's verdicts shouldn't mingle with the old prompt's verdicts in a partially-populated cache. No additional pinning machinery required.

### 4. Migration posture

Existing cache entries on disk use the old string-literal versions (`"2026-05-14-a"`). First run after this change invalidates them all (cache miss → fresh LLM call → cache overwrite). Acceptable; documented in the commit message.

### 5. Version string format

`f"auto:{blake2b(instruction.encode(), digest_size=6).hexdigest()}"` — 12-hex-char digest with an `"auto:"` prefix so a glance at a cache file's `prompt_version` field tells you it was machine-derived rather than a hand-set date string. 6-byte digest gives 2⁴⁸ space — collision-resistance is irrelevant here (we only need inequality with the prior value), but a longer digest is too long for the eye to scan.

---

## File Structure

```
src/agents/analysts/
└── report_cache.py            # MODIFY — add _derive_prompt_version(),
                               # replace string-literal version constants
                               # with computed values at module import time

tests/unit/agents/analysts/
└── test_report_cache_version.py   # CREATE — unit tests for the helper
                                   # + regression that a template edit
                                   # changes the version

docs/superpowers/backlog.md    # MODIFY — mark B23 resolved (~B23~ struck through)

graphify-out/graph_delta.md    # MODIFY — append a dated entry (local only,
                               # NEVER staged — see CLAUDE.md)
```

No changes are required to `src/agents/analysts/news/agent.py` or `src/agents/analysts/fundamental/agent.py` — they already import the version constants by name; the values just become auto-derived rather than hand-set.

No changes are required to existing integration tests (`test_news_cache_*.py`, `test_fundamental_cache_*.py`) — they already pass `prompt_version` as a runtime kwarg via `make_report_cache_callbacks(...)`, and the constant's *name* and *type* are unchanged.

---

## Task 1: `_derive_prompt_version` helper + import-time computation

**Files:**

- Modify: `src/agents/analysts/report_cache.py` lines 37-47 (the prompt-version comment block + the two literal constants)
- Create: `tests/unit/agents/analysts/test_report_cache_version.py`

- [ ] **Step 1: Write the failing unit tests**

Create the file `tests/unit/agents/analysts/test_report_cache_version.py` with the following content:

```python
"""Unit tests for the auto-derived prompt-version fingerprint (B23).

The helper is intentionally pure (string → string).  The module-level
constants are computed at import time from the real rendered prompts —
so the tests cover (a) the helper's algebraic properties and (b) the
sanity of the live constants.
"""
from __future__ import annotations

from agents.analysts.report_cache import (
    FUNDAMENTAL_PROMPT_VERSION,
    NEWS_PROMPT_VERSION,
    _derive_prompt_version,
)


# ---------------------------------------------------------------------------
# Helper behaviour
# ---------------------------------------------------------------------------

def test_derive_prompt_version_is_deterministic():
    """Calling the helper twice on the same string returns the same digest."""
    s = "You are the News analyst. catalysts: ['earnings']..."
    assert _derive_prompt_version(s) == _derive_prompt_version(s)


def test_derive_prompt_version_differs_on_input_change():
    """A one-character change in the instruction changes the digest."""
    a = "You are the News analyst."
    b = "You are the News analyst!"
    assert _derive_prompt_version(a) != _derive_prompt_version(b)


def test_derive_prompt_version_has_auto_prefix():
    """The returned string is prefixed with ``auto:`` so a cache reader can
    tell at a glance that the version is machine-derived rather than a
    hand-set date string.
    """
    out = _derive_prompt_version("anything")
    assert out.startswith("auto:")


def test_derive_prompt_version_digest_length():
    """The digest portion is 12 hex chars (6-byte blake2b)."""
    out = _derive_prompt_version("anything")
    _, digest = out.split(":", 1)
    assert len(digest) == 12
    # All hex digits.
    int(digest, 16)


# ---------------------------------------------------------------------------
# Live constants
# ---------------------------------------------------------------------------

def test_live_news_prompt_version_is_auto_derived():
    """The module-level News version constant is computed by the helper."""
    assert NEWS_PROMPT_VERSION.startswith("auto:")


def test_live_fundamental_prompt_version_is_auto_derived():
    """The module-level Fundamental version constant is computed by the helper."""
    assert FUNDAMENTAL_PROMPT_VERSION.startswith("auto:")


def test_news_and_fundamental_versions_differ():
    """The two analysts have distinct rendered prompts, so they must have
    distinct version fingerprints — otherwise a cache cross-contamination
    risk exists between the two analyst sub-trees.
    """
    assert NEWS_PROMPT_VERSION != FUNDAMENTAL_PROMPT_VERSION
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_report_cache_version.py -v`

Expected: every test fails with `ImportError` on `_derive_prompt_version` (the helper doesn't exist yet).

- [ ] **Step 3: Add the helper and replace the literal constants**

Edit `src/agents/analysts/report_cache.py`. **Replace lines 37-47** — the comment block plus the two literal constants — with the block below.

Note: the existing `from hashlib import blake2b` import on line 26 is reused; no new imports of `hashlib` are needed.

Two **new** imports are added at the top of the file (place them with the existing `from data.models ...` group, alphabetised — Ruff will catch order issues anyway):

```python
from agents.analysts.fundamental.prompts import build_fundamental_instruction
from agents.analysts.heuristics import load_heuristics
from agents.analysts.news.prompts import build_news_instruction
```

Then replace lines 37-47 with:

```python
# ---------------------------------------------------------------------------
# Prompt-version fingerprints — auto-derived from the rendered instruction
# ---------------------------------------------------------------------------
# Each constant is a 6-byte blake2b digest of the rendered prompt
# instruction string with an ``"auto:"`` prefix.  Because the rendered
# string is built by ``build_<analyst>_instruction(vocab)``, it embeds
# (a) the prompt template body, (b) the closed-vocab lists from
# ``analyst_heuristics.json``, and (c) the rationale char-cap from
# ``analysts.json::output_caps``.  Any change to any of those three
# automatically flips the version → every cached entry written under the
# old version is treated as a miss and is overwritten on the next LLM call.
#
# Rationale:
#   Hand-maintained version strings rot — a contributor editing a prompt
#   template has no structural prompt to bump the constant.  Forgetting to
#   bump silently serves stale verdicts generated under the old prompt.
#   Auto-derivation removes the human-discipline failure mode entirely.
#   See backlog entry [[B23]] for the design discussion.
# ---------------------------------------------------------------------------

def _derive_prompt_version(instruction: str) -> str:
    """Compute the cache-key version fingerprint for a rendered prompt.

    Parameters
    ----------
    instruction:
        The fully-rendered instruction string returned by
        ``build_<analyst>_instruction(vocab)``.  Hashing the rendered
        string (rather than the template plus a reference vocab) means a
        change to the template, the closed-vocab lists, or the char-cap
        substitutions all flow into the hash through a single channel.

    Returns
    -------
    str
        A string of the form ``"auto:<12-hex-chars>"`` — a 6-byte
        blake2b digest with a literal ``"auto:"`` prefix that lets
        humans see at a glance the version was machine-derived rather
        than hand-set.  6 bytes is plenty: collision-resistance is
        irrelevant here (we only need inequality with the prior value)
        and a longer digest is too long for the eye to scan.
    """
    return f"auto:{blake2b(instruction.encode(), digest_size=6).hexdigest()}"


# Render each analyst's instruction once at import time using the
# heuristics file's closed-vocab lists, then hash the result.  Both
# ``load_heuristics()`` and the analyst-config singleton consumed inside
# ``build_*_instruction`` are ``lru_cache(maxsize=1)``, so this work is
# cheap and only fires on the first import.

_HEURISTICS = load_heuristics()

#: Version string baked into every News cache entry.  Auto-derived from
#: the rendered News prompt at module import time — see
#: ``_derive_prompt_version`` above.
NEWS_PROMPT_VERSION = _derive_prompt_version(
    build_news_instruction(_HEURISTICS.news_vocabulary)
)

#: Version string baked into every Fundamental cache entry.  Auto-derived
#: from the rendered Fundamental prompt at module import time.
FUNDAMENTAL_PROMPT_VERSION = _derive_prompt_version(
    build_fundamental_instruction(_HEURISTICS.fundamental_vocabulary)
)
```

- [ ] **Step 4: Run the new unit tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/agents/analysts/test_report_cache_version.py -v`

Expected: all 7 tests pass.

- [ ] **Step 5: Run the existing analyst-cache integration tests to verify no regression**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/integration/test_news_cache_roundtrip.py tests/integration/test_news_cache_invalidation.py tests/integration/test_news_cache_prompt_version.py tests/integration/test_fundamental_cache_roundtrip.py tests/integration/test_fundamental_cache_invalidation.py tests/integration/test_fundamental_cache_prompt_version.py -v`

Expected: all integration tests pass. These tests import `NEWS_PROMPT_VERSION` and `FUNDAMENTAL_PROMPT_VERSION` by name and pass them through to `make_report_cache_callbacks(...)` — the values just changed from a date string to an `"auto:..."` digest, which doesn't affect any test's assertions.

- [ ] **Step 6: Run the full unit + integration suites for safety**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/ tests/integration/ -q`

Expected: all tests pass (current baseline is 499 passed + 1 skipped; this change adds 7 unit tests so the new baseline is 506 passed + 1 skipped).

- [ ] **Step 7: Lint check**

Run: `.venv/bin/python -m ruff check src/ tests/`

Expected: no errors. If ruff flags import-order issues (`I001`), run the same command with `--fix`.

- [ ] **Step 8: Commit**

```bash
git add src/agents/analysts/report_cache.py tests/unit/agents/analysts/test_report_cache_version.py
git commit -m "$(cat <<'EOF'
refactor(analysts): auto-derive prompt-version fingerprint (B23)

Replace hand-maintained NEWS_PROMPT_VERSION / FUNDAMENTAL_PROMPT_VERSION
string constants with values computed at import time from a blake2b
digest of each analyst's rendered prompt instruction.  The rendered
string already embeds the prompt template body, the closed-vocab lists
from analyst_heuristics.json, and the rationale char-cap from
analysts.json - so any of those changing automatically flips the
version and invalidates every cached entry.

Removes the silent-stale-cache failure mode where a contributor edits
a prompt template, forgets to bump the version constant, and the cache
serves stale verdicts generated under the old prompt.  No human
discipline required: the cache key is now structurally tied to the
rendered prompt.

Migration: existing cache entries on disk use the old date-string
versions (e.g. "2026-05-14-a") and will all miss on first read after
this lands.  Acceptable - one LLM call per ticker per analyst to
repopulate.  Pre-deployment so no live state to migrate.

No call-site changes - both constants keep their names and types;
downstream code (news/agent.py, fundamental/agent.py, integration
tests) imports them unchanged.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Update backlog + graph_delta

**Files:**

- Modify: `docs/superpowers/backlog.md` — mark [[B23]] resolved
- Modify: `graphify-out/graph_delta.md` — append a dated entry (LOCAL ONLY — never `git add` per CLAUDE.md)

- [ ] **Step 1: Mark B23 resolved in the backlog**

Find the `### B23. Auto-derived prompt-version fingerprint ...` heading in `docs/superpowers/backlog.md` and replace it with the strike-through form, mirroring how [[B22]] was marked resolved on 2026-05-14.

The new heading line:

```markdown
### ~~B23~~. Auto-derived prompt-version fingerprint (close the silent-stale-cache risk)
```

Then **insert** a new paragraph immediately after the heading (before the existing `**Origin:**` line):

```markdown
**Status: resolved (2026-05-14)** — Shipped as a pre-backtest hardening pass: the report-cache version strings in `src/agents/analysts/report_cache.py` are now auto-derived at import time from a blake2b digest of each analyst's rendered prompt instruction. Any edit to a prompt template, the closed-vocab JSON, or the analyst output caps automatically flips the version → all cached entries miss on next read and are overwritten with fresh LLM output. The hand-maintained string constants are gone; the silent-stale-cache risk is closed structurally rather than by human discipline. See commit `refactor(analysts): auto-derive prompt-version fingerprint (B23)`.

```

(Leave the rest of the B23 entry — Origin, The goal, Key questions, Dependencies — in place for posterity, same as B22.)

- [ ] **Step 2: Append a graph_delta entry**

Read the current `graphify-out/graph_delta.md` first to confirm it exists and to learn the most recent format. Then **insert a new dated section at the top of the file** (immediately after the `# graph_delta.md` header line, before the first existing dated section), following the established format:

```markdown
## 2026-05-14 — B23 auto-derived prompt-version fingerprint

Replaced the hand-maintained `NEWS_PROMPT_VERSION` /
`FUNDAMENTAL_PROMPT_VERSION` string literals in
`src/agents/analysts/report_cache.py` with values auto-computed at
import time from a blake2b digest of each analyst's rendered prompt
instruction.  Closes the silent-stale-cache failure mode where a
contributor edits a prompt template and forgets to bump the version.

- New nodes: `src/agents/analysts/report_cache.py::_derive_prompt_version`
  (pure helper, instruction string → "auto:<digest>").
- Changed nodes: module-level constants `NEWS_PROMPT_VERSION` and
  `FUNDAMENTAL_PROMPT_VERSION` are now computed values rather than
  string literals.  Name + type unchanged; all import sites are
  source-compatible.
- New edges: `agents/analysts/report_cache.py` →
  `agents/analysts/news/prompts.py` (`build_news_instruction`);
  `agents/analysts/report_cache.py` →
  `agents/analysts/fundamental/prompts.py`
  (`build_fundamental_instruction`);
  `agents/analysts/report_cache.py` → `agents/analysts/heuristics.py`
  (`load_heuristics`).
- New test node:
  `tests/unit/agents/analysts/test_report_cache_version.py` — 7 unit
  tests covering the helper's determinism, input-sensitivity, prefix
  format, digest length, and the live constants' "auto:" prefix +
  cross-analyst distinctness.
```

- [ ] **Step 3: Verify graph_delta is gitignored**

Run: `git check-ignore -v graphify-out/graph_delta.md`

Expected: the file path prints with the `.gitignore` rule that matches it. If it does **not** print anything (or returns nonzero), STOP — the gitignore convention in `CLAUDE.md` has been violated and the file must be re-ignored before continuing. Do not `git add` `graphify-out/`.

- [ ] **Step 4: Commit the backlog edit (only — graph_delta stays local)**

```bash
git add docs/superpowers/backlog.md
git status
```

Verify the `git status` output lists **only** `docs/superpowers/backlog.md` as staged. If `graphify-out/graph_delta.md` appears as staged, run `git restore --staged graphify-out/graph_delta.md` before continuing.

Then:

```bash
git commit -m "$(cat <<'EOF'
docs(backlog): mark B23 (auto prompt-version fingerprint) resolved

Shipped in the prior commit as a pre-backtest hardening pass.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**Spec coverage check.** The three open key questions in the backlog entry ([[B23]]) map to:

- *What to hash?* → answered in mini-spec §1 ("rendered instruction only"), implemented in Task 1 Step 3 via `_derive_prompt_version(build_*_instruction(...))`.
- *Reference-vocab problem?* → answered in mini-spec §2 ("no reference vocab; use real vocab from `load_heuristics()`"), implemented in Task 1 Step 3 via `_HEURISTICS = load_heuristics()` at module level.
- *Backtest compatibility?* → answered in mini-spec §3 ("auto-pinned by import time"), no implementation needed.
- *Migration?* → answered in mini-spec §4, called out in the commit message.
- *Where to live?* → answered in File Structure section and Task 1 Step 3 ("`src/agents/analysts/report_cache.py`, a `_derive_prompt_version` helper, constants computed at import time").

**Placeholder scan.** No "TBD" / "TODO" / "fill in details" / "similar to Task N" markers in the plan. Each step has the actual content the engineer needs.

**Type consistency.** The helper signature `_derive_prompt_version(instruction: str) -> str` is identical in every reference (mini-spec §5, Task 1 Step 1 test imports, Task 1 Step 3 implementation, Task 2 Step 2 graph_delta entry). Constant names `NEWS_PROMPT_VERSION` / `FUNDAMENTAL_PROMPT_VERSION` are unchanged from current code — verified by grep against `src/agents/analysts/news/agent.py:44`, `src/agents/analysts/fundamental/agent.py:41`, and the six `tests/integration/test_*_cache_*.py` files.

**Risk register:**

- *Circular-import risk.* Confirmed clean: `news/prompts.py` and `fundamental/prompts.py` import only from `agents.analysts.heuristics` and `config.analysts` — neither imports `report_cache`. `heuristics.py` imports nothing from the analyst sub-tree. No cycle introduced.
- *Import-time cost.* `load_heuristics()` and `get_analysts_config()` are both `lru_cache(maxsize=1)`; calling them at module import adds one JSON parse each on first import only. Negligible.
- *Test environment.* The two unit tests of the *live* constants (`test_live_news_prompt_version_is_auto_derived`, `test_live_fundamental_prompt_version_is_auto_derived`) require `config/analyst_heuristics.json` and `config/analysts.json` to be present in the cwd — both are checked in, so this is the project's standard test environment.

---

## Execution Handoff

Plan complete and saved to `docs/Phase5-analyst-refine/plan-b23-auto-prompt-version.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task with two-stage review (spec-compliance + code-quality) between tasks. Fast iteration; preserves the controller's context.

**2. Inline Execution** — execute the two tasks in this session via `superpowers:executing-plans`, batched with checkpoints.

Which approach?
